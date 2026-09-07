"""Script for creating the hermetic 50-commune test sample dataset for ODIS.

Filters the active production parquets down to a curated, stratified sample
of 50 metropolitan communes, covering demographic extremes, territorial specificities,
CCAS search logic paths, and scoring invariants.
"""

from pathlib import Path
from typing import List, Set
import logging
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("create_test_sample")

COMMUNES_50_METROPOLITAINES: List[str] = [
    # 36 codes preserved from existing unit and E2E test suites
    "75056",  # Paris (75)
    "13055",  # Marseille (13)
    "69123",  # Lyon (69)
    "44109",  # Nantes (44)
    "33063",  # Bordeaux (33)
    "13001",  # Aix-en-Provence (13)
    "72181",  # Le Mans (72)
    "64445",  # Pau (64)
    "33281",  # Mérignac (33)
    "94041",  # Ivry-sur-Seine (94)
    "69290",  # Saint-Priest (69)
    "11069",  # Carcassonne (11)
    "24322",  # Périgueux (24)
    "24037",  # Bergerac (24)
    "33119",  # Cenon (33)
    "33069",  # Le Bouscat (33)
    "13002",  # Allauch (13)
    "33167",  # Floirac (33)
    "33003",  # Ambarès-et-Lagrave (33)
    "44055",  # La Baule-Escoublac (44)
    "01004",  # Ambérieu-en-Bugey (01)
    "33200",  # Le Haillan (33)
    "33009",  # Arcachon (33)
    "17347",  # Saint-Jean-d'Angély (17)
    "40001",  # Aire-sur-l'Adour (40)
    "33004",  # Ambès (33)
    "33067",  # Bourg (33)
    "33001",  # Abzac (33)
    "01001",  # L'Abergement-Clémenciat (01)
    "33002",  # Aillas (33)
    "33150",  # Dieulivol (33)
    "45032",  # Le Bignon-Mirabeau (45)
    "01002",  # L'Abergement-de-Varey (01)
    "64001",  # Aast (64)
    "33270",  # Marimbault (33)
    "16215",  # Médillac (16)
    # 14 strategic additions (100% metropolitan, 0 DROM)
    "35238",  # Rennes (35) - ANVITA & métropole Grand Ouest
    "16015",  # Angoulême (16) - Ville moyenne Nouvelle-Aquitaine
    "34172",  # Montpellier (34) - ANVITA & Occitanie
    "67482",  # Strasbourg (67) - ANVITA & Grand Est
    "59350",  # Lille (59) - ANVITA & Hauts-de-France
    "29019",  # Brest (29) - Littoral Finistère
    "50129",  # Cherbourg-en-Cotentin (50) - Commune nouvelle majeure
    "2A004",  # Ajaccio (2A) - Préfecture Corse-du-Sud
    "2B033",  # Bastia (2B) - Préfecture Haute-Corse
    "2B096",  # Corte (2B) - Ville universitaire Corse montagne
    "23096",  # Guéret (23) - Ruralité / Creuse
    "48095",  # Mende (48) - Ruralité / Lozère (repli BV)
    "85223",  # Sainte-Hermine (85) - Vendée, 0 CCAS dans tout le BV
    "21222",  # Cussy-le-Châtel (21) - Côte-d'Or (124 hab), 0 CCAS dans tout le BV
]


def extract_sample_datasets(
    src_dir: Path = Path("app/data/datasets/active"),
    dest_dir: Path = Path("tests/fixtures/data"),
) -> None:
    """Extract sample parquet files for the 50 target metropolitan communes.

    Args:
        src_dir: Directory containing active production parquet files.
        dest_dir: Destination directory for fixture files.

    Raises:
        FileNotFoundError: If source active dataset directory is missing.
    """
    if not src_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {src_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    target_codes: Set[str] = set(COMMUNES_50_METROPOLITAINES)

    logger.info(
        "Starting sample dataset extraction for %d target communes...",
        len(target_codes),
    )

    # 1. odis_communes.parquet
    communes_src = src_dir / "odis_communes.parquet"
    communes_dest = dest_dir / "sample_communes.parquet"
    df_communes = pd.read_parquet(communes_src)
    sample_communes = df_communes[df_communes["codgeo"].isin(target_codes)].copy()
    sample_communes.to_parquet(communes_dest, index=False)
    logger.info(
        "Saved %s (%d rows, %.1f KB)",
        communes_dest,
        len(sample_communes),
        communes_dest.stat().st_size / 1024,
    )

    # Collect Bassins de Vie associated with the 50 communes for relational fidelity
    sample_bvs: Set[str] = set(sample_communes["bassin_de_vie"].dropna().unique())
    bv_communes: Set[str] = set(
        df_communes[df_communes["bassin_de_vie"].isin(sample_bvs)]["codgeo"].unique()
    )

    # 2. odis_ccas.parquet (include local CCAS and Bassin de Vie CCAS for fallback fidelity)
    ccas_src = src_dir / "odis_ccas.parquet"
    if ccas_src.exists():
        ccas_dest = dest_dir / "sample_ccas.parquet"
        df_ccas = pd.read_parquet(ccas_src)
        sample_ccas = df_ccas[
            df_ccas["codgeo"].astype(str).isin(bv_communes)
        ].copy()
        sample_ccas.to_parquet(ccas_dest, index=False)
        logger.info(
            "Saved %s (%d rows, %.1f KB)",
            ccas_dest,
            len(sample_ccas),
            ccas_dest.stat().st_size / 1024,
        )

    # 3. odis_inclusion_jobs.parquet
    inc_src = src_dir / "odis_inclusion_jobs.parquet"
    if inc_src.exists():
        inc_dest = dest_dir / "sample_inclusion_jobs.parquet"
        df_inc = pd.read_parquet(inc_src)
        sample_inc = df_inc[df_inc["codgeo"].astype(str).isin(target_codes)].copy()
        sample_inc.to_parquet(inc_dest, index=False)
        logger.info(
            "Saved %s (%d rows, %.1f KB)",
            inc_dest,
            len(sample_inc),
            inc_dest.stat().st_size / 1024,
        )

    logger.info("Sample extraction completed successfully in %s", dest_dir)


if __name__ == "__main__":
    extract_sample_datasets()
