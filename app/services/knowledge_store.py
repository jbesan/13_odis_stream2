import os
import logging
from typing import List, Dict, Any, Optional
import frontmatter
import config as cfg

logger = logging.getLogger("KnowledgeStore")

class KnowledgeStore:
    """
    Service to manage ODIS Skill Cards using Markdown files with YAML frontmatter.
    Stored under 'app/agents/skills/' relative to the project root.
    """

    def __init__(self, db_path: Optional[str] = None, skills_dir: Optional[str] = None):
        if skills_dir is None:
            if db_path is not None:
                # If db_path is passed (e.g. in tests like '/tmp/test_knowledge.db'),
                # we use a directory named 'skills' in the same folder as the db file.
                skills_dir = os.path.join(os.path.dirname(db_path), "skills")
            else:
                # Default path: app/agents/skills/ relative to the project root
                skills_dir = os.path.join(cfg.PROJECT_ROOT, "app", "agents", "skills")
        
        self.skills_dir = skills_dir
        self.db_path = db_path  # Keep for backward compatibility / tests checking store.db_path
        self._init_db()

    def _init_db(self):
        """Initializes the skills directory and touches self.db_path if provided."""
        try:
            os.makedirs(self.skills_dir, exist_ok=True)
            logger.info(f"Skills directory initialized at {self.skills_dir}")
            if self.db_path:
                os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
                with open(self.db_path, "a") as f:
                    pass
        except Exception as e:
            logger.error(f"Failed to initialize skills directory: {e}")
            raise

    def get_skill_card(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single Skill Card by its ID from a Markdown file."""
        file_path = os.path.join(self.skills_dir, f"{skill_id}.md")
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
            return {
                "id": skill_id,
                "name": post.get("name", ""),
                "description": post.get("description", ""),
                "domain": post.get("domain", ""),
                "tools": post.get("tools", []),
                "instructions": post.content.strip()
            }
        except Exception as e:
            logger.error(f"Error fetching skill card '{skill_id}': {e}")
            return None

    def get_skills_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        """Retrieves all Skill Cards belonging to a specific domain."""
        try:
            cards = []
            if not os.path.exists(self.skills_dir):
                return cards
            for filename in sorted(os.listdir(self.skills_dir)):
                if filename.endswith(".md"):
                    skill_id = filename[:-3]
                    card = self.get_skill_card(skill_id)
                    if card and card.get("domain") == domain:
                        cards.append(card)
            return cards
        except Exception as e:
            logger.error(f"Error fetching skills for domain '{domain}': {e}")
            return []

    def get_all_skills(self) -> List[Dict[str, Any]]:
        """Retrieves all Skill Cards stored in the directory."""
        try:
            cards = []
            if not os.path.exists(self.skills_dir):
                return cards
            for filename in sorted(os.listdir(self.skills_dir)):
                if filename.endswith(".md"):
                    skill_id = filename[:-3]
                    card = self.get_skill_card(skill_id)
                    if card:
                        cards.append(card)
            return cards
        except Exception as e:
            logger.error(f"Error fetching all skills: {e}")
            return []

    def insert_or_update_skill(self, skill_id: str, description: str, instructions: str, domain: str, name: Optional[str] = None, version: Optional[str] = None, tags: Optional[List[str]] = None, tools: Optional[List[str]] = None):
        """Inserts a new Skill Card or updates an existing one as a Markdown file."""
        try:
            file_path = os.path.join(self.skills_dir, f"{skill_id}.md")
            
            # Create a Post object with metadata frontmatter and instructions body
            # If the file already exists, we preserve extra metadata fields like name, version, tags
            existing_metadata = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        post = frontmatter.load(f)
                        existing_metadata = post.metadata
                except Exception:
                    pass
            
            metadata = {
                **existing_metadata,
                "id": skill_id,
                "description": description,
                "domain": domain
            }
            
            if name is not None:
                metadata["name"] = name
            elif "name" not in metadata:
                metadata["name"] = skill_id.replace("_", " ").title()

            if version is not None:
                metadata["version"] = version
            elif "version" not in metadata:
                metadata["version"] = "1.0.0"

            if tags is not None:
                metadata["tags"] = tags
            elif "tags" not in metadata:
                metadata["tags"] = []

            if tools is not None:
                metadata["tools"] = tools
            elif "tools" not in metadata:
                metadata["tools"] = []

            post = frontmatter.Post(content=instructions, **metadata)
            
            with open(file_path, "w", encoding="utf-8") as f:
                frontmatter.dump(post, f)
                
            logger.debug(f"Skill '{skill_id}' stored successfully at {file_path}.")
        except Exception as e:
            logger.error(f"Error inserting/updating skill '{skill_id}': {e}")
            raise
