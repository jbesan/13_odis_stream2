from typing import Any
import pandas as pd
import streamlit as st


def _on_ccas_dialog_dismiss():
    st.session_state.active_ccas_index = None


@st.dialog(
    "Centre Communal d'Action Sociale",
    width="large",
    on_dismiss=_on_ccas_dialog_dismiss,
)
def show_ccas_dialog(index: Any):
    """Displays CCAS/CIAS local contact information in a dialog modal."""
    if (
        "search_results" not in st.session_state
        or not st.session_state.search_results
        or not st.session_state.search_results.get_by_code(index)
    ):
        st.error("Données de la ville introuvables.")
        return

    commune = st.session_state.search_results.get_by_code(index)
    codgeo = commune.codgeo
    libgeo = commune.name
    # The Results page owns a complete, single app-data snapshot. Reuse it here
    # instead of triggering an unrelated loader call from a dialog.
    app_data = st.session_state.get("app_data", {})
    structures_df = app_data.get("structures_ccas", pd.DataFrame())

    target_codes = [codgeo.strip()]
    # Optional logic for binome if needed (fallback to df_all_communes)
    df_all = app_data.get("odis", pd.DataFrame())
    if codgeo in df_all.index:
        row = df_all.loc[codgeo]
        if "binome" in row and row["binome"] and "codgeo_binome" in row:
            target_codes.append(str(row["codgeo_binome"]).strip())

    if not structures_df.empty and "codgeo" in structures_df.columns:
        # Filter with clean string types
        subset = structures_df[structures_df["codgeo"].isin(target_codes)].copy()

        if not subset.empty:
            for _, struct in subset.iterrows():
                st.divider()
                # Layout: Commune First
                label = struct["commune"] if pd.notna(struct.get("commune")) else libgeo
                st.subheader(f"📍 {label}")

                # Name
                st.write(f"**{struct['nom']}**")

                # Address
                if pd.notna(struct.get("adresse")):
                    st.write(f"{struct['adresse']}")

                # Contact Info
                c1, c2 = st.columns(2)
                with c1:
                    if pd.notna(struct.get("telephone")):
                        st.write(f"📞 {struct['telephone']}")
                with c2:
                    if pd.notna(struct.get("courriel")):
                        # Simple email link
                        st.markdown(
                            f"✉️ [{struct['courriel']}](mailto:{struct['courriel']})"
                        )

                if pd.notna(struct.get("site_web")):
                    st.markdown(f"🌐 [Site Web]({struct['site_web']})")

        else:
            st.info(
                f"Aucune structure CCAS/CIAS référencée (avec contact) pour {libgeo}."
            )
    else:
        st.warning("Données structures non disponibles.")
