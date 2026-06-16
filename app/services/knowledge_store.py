import sqlite3
import os
import logging
from typing import List, Dict, Any, Optional
import config as cfg

logger = logging.getLogger("KnowledgeStore")

class KnowledgeStore:
    """
    Service to manage ODIS Skill Cards.
    Stores metadata, descriptions, instructions, and target domains in a standard SQLite database.
    Does not require compiled binary extensions, ensuring portability.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.path.join(cfg.LOCAL_DATA_PATH, "knowledge.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initializes the SQLite database and creates the skills table if it does not exist."""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY,
                    description TEXT,
                    instructions TEXT,
                    domain TEXT
                )
            """)
            conn.commit()
            conn.close()
            logger.info(f"Database initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def get_skill_card(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single Skill Card by its ID."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, description, instructions, domain FROM skills WHERE id = ?", (skill_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.error(f"Error fetching skill card '{skill_id}': {e}")
            return None

    def get_skills_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        """Retrieves all Skill Cards belonging to a specific domain."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, description, instructions, domain FROM skills WHERE domain = ?", (domain,))
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching skills for domain '{domain}': {e}")
            return []

    def get_all_skills(self) -> List[Dict[str, Any]]:
        """Retrieves all Skill Cards stored in the database."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, description, instructions, domain FROM skills")
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching all skills: {e}")
            return []

    def insert_or_update_skill(self, skill_id: str, description: str, instructions: str, domain: str):
        """Inserts a new Skill Card or updates an existing one if the ID conflicts."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO skills (id, description, instructions, domain)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    description = excluded.description,
                    instructions = excluded.instructions,
                    domain = excluded.domain
            """, (skill_id, description, instructions, domain))
            conn.commit()
            conn.close()
            logger.debug(f"Skill '{skill_id}' stored successfully.")
        except Exception as e:
            logger.error(f"Error inserting/updating skill '{skill_id}': {e}")
            raise
