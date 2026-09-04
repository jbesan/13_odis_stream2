"""Streamlit-native adapter between widget state and ``SearchCriterias``.

Widget values intentionally remain top-level entries in ``st.session_state``:
that is Streamlit's native state model.  This adapter provides the missing
convention around those entries so defaults, demo/org profiles, shared-search
restoration and form submission all use the same mapping.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

import pandas as pd

import config as cfg
from core.models import CriteriaItem, SearchAreaLevel, SearchCriterias


FORM_INITIALIZED_KEY = "_form_state_initialized"
EDITOR_SOURCE_HASH_KEY = "_criteria_editor_source_hash"
EDITOR_WIDGET_KEYS = ("ui_commune",)


def _safe_option(value: str) -> str:
    return (
        value.replace(" ", "_")
        .replace("'", "_")
        .replace("(", "")
        .replace(")", "")
        .lower()
    )


def housing_key(option: str) -> str:
    return f"ui_heb_cb_{_safe_option(option)}"


def long_term_housing_key(option: str) -> str:
    return f"ui_logement_cb_{_safe_option(option)}"


def health_key(option: str) -> str:
    return f"ui_sante_cb_{_safe_option(option)}"


def _code(value: Any) -> Any:
    if isinstance(value, Mapping) and "code" in value:
        return value["code"]
    if hasattr(value, "code"):
        return value.code
    return value


def _codes(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    return [str(_code(value)) for value in values if _code(value) is not None]


def _label_from_index(index: pd.DataFrame, code: Any) -> str:
    if not index.empty and code in index.index:
        value = index.loc[code, "label"]
        if isinstance(value, pd.Series):
            value = value.iloc[0]
        return str(value)
    return str(code)


class FormState:
    """Owns the mapping rules for ODIS form widget keys."""

    FIELD_KEYS = {
        "commune_actuelle": "ui_commune",
        "nb_adultes": "ui_nb_adultes",
        "nb_enfants": "ui_nb_enfants",
        "logement": "ui_logement",
        "type_logement": "ui_type_logement",
        "freq_retour": "ui_freq_retour",
        "notes_qualitatives": "ui_notes_qualitatives",
        "weight_profile": "ui_weight_profile",
        "org_strategic_locations": "ui_org_strategic_locations",
        "org_strategic_locations_type": "ui_org_strategic_locations_type",
        "org_strategic_locations_filter": "ui_org_strategic_locations_filter",
        "poids_emploi": "ui_poids_emploi",
        "poids_logement": "ui_poids_logement",
        "poids_education": "ui_poids_education",
        "poids_inclusion": "ui_poids_inclusion",
        "poids_sante": "ui_poids_sante",
        "poids_mobilite": "ui_poids_mobilite",
        "poids_territoire": "ui_poids_territoire",
    }

    def __init__(self, state: MutableMapping[str, Any]):
        self.state = state

    def _put(self, key: str, value: Any, *, overwrite: bool) -> None:
        if overwrite or key not in self.state:
            self.state[key] = value

    def initialize(self, defaults: Mapping[str, Any]) -> None:
        """Initialize missing widgets once without overwriting user edits."""
        self.hydrate(defaults, overwrite=False)
        self.state[FORM_INITIALIZED_KEY] = True

    def preserve_widgets_across_steps(self) -> None:
        """Interrupt Streamlit cleanup while a page temporarily hides form widgets.

        Streamlit removes state for widgets that are not rendered in a run.
        Self-assignment is its documented multipage persistence convention;
        keeping it here makes the workaround explicit and centrally scoped.
        """
        for key in list(self.state):
            if str(key).startswith("ui_"):
                self.state[key] = self.state[key]

    def prepare_editor(
        self,
        criteria: SearchCriterias | Mapping[str, Any] | Any,
        *,
        source_hash: str,
        app_data: Mapping[str, Any] | None = None,
    ) -> bool:
        """Prepare a criteria editor without overwriting an existing draft.

        Results normally keeps the native ``ui_*`` keys alive while the form is
        hidden.  If a page transition or a restored snapshot has removed them,
        the immutable criteria of the active search is the only authoritative
        source from which to rebuild the editor.  A new source hash also means
        a new completed search, so it deliberately replaces the former draft.

        This method is called only while opening the dialog, never from the
        dialog body: Streamlit re-runs a dialog fragment for every interaction.
        """
        draft_is_materialized = all(key in self.state for key in EDITOR_WIDGET_KEYS)
        if (
            self.state.get(EDITOR_SOURCE_HASH_KEY) == source_hash
            and draft_is_materialized
        ):
            return False

        self.hydrate(criteria, app_data=app_data, overwrite=True)
        self.state[EDITOR_SOURCE_HASH_KEY] = source_hash
        return True

    def hydrate(
        self,
        criteria: SearchCriterias | Mapping[str, Any] | Any,
        *,
        app_data: Mapping[str, Any] | None = None,
        overwrite: bool = True,
        exclude_unset: bool = True,
    ) -> None:
        """Load defaults, AI criteria or a shared search into widget state.

        This method must be called before the corresponding widgets are
        rendered in the current Streamlit run.
        """
        if criteria is None:
            return
        if isinstance(criteria, Mapping):
            values = dict(criteria)
        elif hasattr(criteria, "model_dump"):
            values = criteria.model_dump(exclude_unset=exclude_unset)
        else:
            values = dict(vars(criteria))

        bundle = app_data or self.state.get("app_data", {})

        for field, key in self.FIELD_KEYS.items():
            if field not in values:
                continue
            value = values[field]
            if value is None:
                self._put(key, None, overwrite=overwrite)
                continue
            if field == "commune_actuelle":
                code = _code(value)
                value = str(code) if code is not None else None
            elif field == "type_logement":
                value = _code(value)
            elif field == "notes_qualitatives" and isinstance(value, list):
                value = "\n".join(str(item) for item in value)
            self._put(key, value, overwrite=overwrite)

        housing = values.get("hebergement_cible")
        if housing is not None:
            selected = set(housing if isinstance(housing, list) else [housing])
            for option in cfg.HEBERGEMENT_OPTIONS:
                self._put(housing_key(option), option in selected, overwrite=overwrite)

        logement_val = values.get("logement")
        if logement_val is not None:
            selected_logement = set(
                logement_val if isinstance(logement_val, list) else [logement_val]
            )
            for option in cfg.LOGEMENT_OPTIONS:
                self._put(
                    long_term_housing_key(option),
                    option in selected_logement,
                    overwrite=overwrite,
                )

        health = values.get("besoin_sante", values.get("sante"))
        if health is not None:
            selected = set(health if isinstance(health, list) else [health])
            for option in cfg.SANTE_OPTIONS:
                self._put(health_key(option), option in selected, overwrite=overwrite)

        for field, key_base in (
            ("classe_enfants", "ui_classe_enfant"),
            ("codes_metiers", "ui_metiers_adult"),
            ("codes_formations", "ui_formations_adult"),
        ):
            if field not in values or not isinstance(values[field], list):
                continue
            self._clear_dynamic(key_base)
            for index, item in enumerate(values[field]):
                value = _codes(item) if field != "classe_enfants" else item
                self._put(f"{key_base}_{index}", value, overwrite=True)

        area = values.get("loc_search_area", SearchAreaLevel.DEPARTEMENT)
        self._put("ui_loc_search_area", area, overwrite=overwrite)
        codes = _codes(values.get("loc_search_code", []))
        area_str = str(area).lower()
        if area == SearchAreaLevel.REGION or area_str == "region":
            self._put("ui_mobility_region", codes, overwrite=overwrite)
        elif area == SearchAreaLevel.DEPARTEMENT or area_str == "departement":
            self._put("ui_mobility_dept", codes, overwrite=overwrite)

        pressentie = values.get("commune_pressentie")
        if pressentie is not None:
            code = _code(pressentie)
            self._put("ui_has_commune_pressentie", bool(code), overwrite=overwrite)
            self._put("ui_commune_pressentie", code, overwrite=overwrite)

        if "inc_services_selection" in values:
            self._put(
                "ui_inc_services_selection_raw",
                _codes(values["inc_services_selection"]),
                overwrite=overwrite,
            )
        if "inc_asso_add_selection" in values:
            self._put(
                "ui_inc_asso_add_selection_raw",
                _codes(values["inc_asso_add_selection"]),
                overwrite=overwrite,
            )

        boosts = values.get("org_boosts")
        if isinstance(boosts, Mapping):
            for criterion_id, value in boosts.items():
                self._put(
                    f"ui_org_boost_slider_{criterion_id}",
                    int(value),
                    overwrite=overwrite,
                )

        target_label = values.get("target_city_size") or values.get("ui_target_city_size_label")
        if target_label and target_label in cfg.CITY_SIZE_MAPPING:
            self._put("ui_target_city_size_label", target_label, overwrite=overwrite)
        elif "target_population_a" in values:
            a = values["target_population_a"]
            b = values["target_population_b"]
            c = values["target_population_c"]
            d = values["target_population_d"]
            for label, mapping in cfg.CITY_SIZE_MAPPING.items():
                if mapping["a"] == a and mapping["b"] == b and mapping["c"] == c and mapping["d"] == d:
                    self._put("ui_target_city_size_label", label, overwrite=overwrite)
                    break

        profile = values.get("weight_profile")
        explicit_weights = {
            key: float(value)
            for key, value in values.items()
            if key.startswith("poids_") and value is not None
        }
        if profile in cfg.WEIGHT_PROFILES:
            for key, value in cfg.WEIGHT_PROFILES[profile].items():
                self._put(f"ui_{key}", value, overwrite=overwrite)
        for key, value in explicit_weights.items():
            self._put(f"ui_{key}", value, overwrite=overwrite)
        if explicit_weights and profile in cfg.WEIGHT_PROFILES:
            expected = cfg.WEIGHT_PROFILES[profile]
            if any(expected.get(key) != value for key, value in explicit_weights.items()):
                self._put(
                    "ui_weight_profile", "Profil personnalisé", overwrite=overwrite
                )

        # Use an already loaded complete bundle to infer the region for a
        # restored department search.
        if area == "departement" and codes:
            dept_details = bundle.get("dept_details", {}) if bundle else {}
            region = dept_details.get(codes[0], {}).get("reg_code")
            if region:
                self._put("ui_mobility_region", [region], overwrite=overwrite)

    def _clear_dynamic(self, key_base: str) -> None:
        prefix = f"{key_base}_"
        for key in list(self.state):
            if str(key).startswith(prefix):
                del self.state[key]

    def selected_housing(self) -> list[str]:
        has_any_key = any(
            housing_key(option) in self.state for option in cfg.HEBERGEMENT_OPTIONS
        )
        if not has_any_key:
            default_val = cfg.DEMO_DATA_DEFAULT.get("hebergement_cible", [])
            return list(
                default_val if isinstance(default_val, list) else [default_val]
            )
        return [
            option
            for option in cfg.HEBERGEMENT_OPTIONS
            if self.state.get(housing_key(option), False)
        ]

    def selected_long_term_housing(self) -> list[str]:
        has_any_key = any(
            long_term_housing_key(option) in self.state
            for option in cfg.LOGEMENT_OPTIONS
        )
        if not has_any_key:
            default_val = cfg.DEMO_DATA_DEFAULT.get("logement", ["Location"])
            return list(
                default_val if isinstance(default_val, list) else [default_val]
            )
        return [
            option
            for option in cfg.LOGEMENT_OPTIONS
            if self.state.get(long_term_housing_key(option), False)
        ]

    def selected_health(self) -> list[str]:
        has_any_key = any(
            health_key(option) in self.state for option in cfg.SANTE_OPTIONS
        )
        if not has_any_key:
            default_val = cfg.DEMO_DATA_DEFAULT.get(
                "besoin_sante", cfg.DEMO_DATA_DEFAULT.get("sante", [])
            )
            return list(
                default_val if isinstance(default_val, list) else [default_val]
            )
        return [
            option
            for option in cfg.SANTE_OPTIONS
            if self.state.get(health_key(option), False)
        ]

    def get_location_validation_errors(self) -> list[str]:
        """Validate required location criteria (origin commune & destination search area).

        Returns:
            List of user-facing error messages detailing missing mandatory criteria.
            Empty list if all mandatory criteria are valid.
        """
        errors: list[str] = []

        # 1. Point de départ (commune actuelle)
        commune = self.state.get("ui_commune")
        if not commune:
            errors.append(
                "Point de départ : veuillez sélectionner une ville actuelle."
            )

        # 2. Zone de recherche
        loc_area = self.state.get("ui_loc_search_area", SearchAreaLevel.DEPARTEMENT)
        area_str = str(loc_area).lower()
        if loc_area == SearchAreaLevel.REGION or area_str == "region":
            if not _codes(self.state.get("ui_mobility_region", [])):
                errors.append(
                    "Zone de recherche : veuillez sélectionner au moins une région cible."
                )
        elif loc_area == SearchAreaLevel.DEPARTEMENT or area_str == "departement":
            if not _codes(self.state.get("ui_mobility_dept", [])):
                errors.append(
                    "Zone de recherche : veuillez sélectionner au moins un département cible."
                )

        return errors

    def collect(self, app_data: Mapping[str, Any]) -> SearchCriterias:
        """Build the immutable domain input from the current widget values."""
        commune_val = self.state.get("ui_commune")
        commune = None
        if commune_val:
            code = str(commune_val)
            commune_names = app_data.get("commune_names", {})
            odis = app_data.get("odis", pd.DataFrame())
            label = (
                odis.loc[code, "libgeo"]
                if not odis.empty and code in odis.index
                else commune_names.get(code, code)
            )
            commune = CriteriaItem(code=code, label=str(label))

        commune_pressentie = None
        if self.state.get("ui_has_commune_pressentie") and self.state.get(
            "ui_commune_pressentie"
        ):
            code = str(self.state["ui_commune_pressentie"])
            commune_names = app_data.get("commune_names", {})
            odis = app_data.get("odis", pd.DataFrame())
            label = (
                odis.loc[code, "libgeo"]
                if not odis.empty and code in odis.index
                else commune_names.get(code, code)
            )
            commune_pressentie = CriteriaItem(code=code, label=str(label))

        loc_area = self.state.get("ui_loc_search_area", SearchAreaLevel.DEPARTEMENT)
        area_str = str(loc_area).lower()
        if loc_area == SearchAreaLevel.FRANCE or area_str == "france":
            area, area_codes = SearchAreaLevel.FRANCE, []
        elif loc_area == SearchAreaLevel.REGION or area_str == "region":
            area = SearchAreaLevel.REGION
            area_codes = _codes(self.state.get("ui_mobility_region", []))
        else:
            area = SearchAreaLevel.DEPARTEMENT
            area_codes = _codes(self.state.get("ui_mobility_dept", []))

        children_count = int(self.state.get("ui_nb_enfants", 0))
        child_classes = [
            str(
                self.state.get(
                    f"ui_classe_enfant_{index}",
                    cfg.CLASSES_SCOLAIRES[0] if cfg.CLASSES_SCOLAIRES else "",
                )
            )
            for index in range(children_count)
        ]

        adults_count = int(self.state.get("ui_nb_adultes", 1))
        rome_index = app_data.get("rome_index", pd.DataFrame())
        formation_index = app_data.get("codformations_index", pd.DataFrame())
        jobs = [
            [
                CriteriaItem(code=code, label=_label_from_index(rome_index, code))
                for code in _codes(self.state.get(f"ui_metiers_adult_{index}", []))
            ]
            for index in range(adults_count)
        ]
        formations = [
            [
                CriteriaItem(
                    code=code, label=_label_from_index(formation_index, code)
                )
                for code in _codes(
                    self.state.get(f"ui_formations_adult_{index}", [])
                )
            ]
            for index in range(adults_count)
        ]

        inclusion_index = app_data.get("inclusion_services_index", pd.DataFrame())
        inclusion = [
            CriteriaItem(
                code=code, label=_label_from_index(inclusion_index, code)
            )
            for code in sorted(
                set(_codes(self.state.get("ui_inc_services_selection_raw", [])))
            )
        ]
        waldec_index = app_data.get("waldec_index", pd.DataFrame())
        associations = [
            CriteriaItem(code=code, label=_label_from_index(waldec_index, code))
            for code in _codes(self.state.get("ui_inc_asso_add_selection_raw", []))
        ]

        selected_city_label = self.state.get(
            "ui_target_city_size_label", cfg.DEFAULT_CITY_SIZE
        )
        trapezoid = cfg.CITY_SIZE_MAPPING.get(
            selected_city_label, cfg.DEFAULT_TRAPEZOID
        )
        housing_type_code = self.state.get("ui_type_logement", "appt_all")
        housing_type = (
            CriteriaItem(
                code=str(housing_type_code),
                label=cfg.HOUSING_TYPE_OPTIONS[housing_type_code],
            )
            if housing_type_code in cfg.HOUSING_TYPE_OPTIONS
            else None
        )

        org = self.state.get("org")
        org_defaults = getattr(org, "defaults", {}) if org else {}
        boosts = {
            criterion_id: float(
                self.state.get(
                    f"ui_org_boost_slider_{criterion_id}", default_value
                )
            )
            for criterion_id, default_value in org_defaults.get(
                "org_boosts", {}
            ).items()
        }

        notes = str(self.state.get("ui_notes_qualitatives", "") or "")
        return SearchCriterias(
            weight_profile=self.state.get("ui_weight_profile", "Équilibré"),
            poids_emploi=self.state.get("ui_poids_emploi", 0.5),
            poids_logement=self.state.get("ui_poids_logement", 0.5),
            poids_education=self.state.get("ui_poids_education", 0.5),
            poids_inclusion=self.state.get("ui_poids_inclusion", 0.5),
            poids_sante=self.state.get("ui_poids_sante", 0.5),
            poids_mobilite=self.state.get("ui_poids_mobilite", 0.5),
            criteria_weights={},
            target_city_size=selected_city_label,
            target_population_a=trapezoid["a"],
            target_population_b=trapezoid["b"],
            target_population_c=trapezoid["c"],
            target_population_d=trapezoid["d"],
            commune_actuelle=commune,
            commune_pressentie=commune_pressentie,
            loc_search_area=area,
            loc_search_code=area_codes,
            nb_adultes=adults_count,
            nb_enfants=children_count,
            hebergement_cible=self.selected_housing(),
            logement=self.selected_long_term_housing(),
            type_logement=housing_type,
            freq_retour=self.state.get(
                "ui_freq_retour", "Pas d'attache particulière"
            ),
            codes_metiers=jobs,
            codes_formations=formations,
            classe_enfants=child_classes,
            besoin_sante=self.selected_health(),
            inc_services_selection=inclusion,
            inc_asso_add_selection=associations,
            notes_qualitatives=[notes] if notes else [],
            org_context=org.id if org else None,
            org_strategic_locations=self.state.get(
                "ui_org_strategic_locations", []
            ),
            org_strategic_locations_type=self.state.get(
                "ui_org_strategic_locations_type", "departement"
            ),
            org_strategic_locations_filter=self.state.get(
                "ui_org_strategic_locations_filter", False
            ),
            org_boosts=boosts,
            poids_territoire=self.state.get("ui_poids_territoire", 1.0),
        )
