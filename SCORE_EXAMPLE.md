# Step-by-Step Scoring Calculation for Demo 3 (Aïcha)

This document details how the scoring algorithm calculates the suitability of a commune for the "Demo 3" scenario.

## 1. User Profile & Preferences (Demo 3)

- **Name**: Aïcha
- **Current Location**: Marseille (13)
- **Search Radius**: 50 km
- **Family**: 1 Adult, 2 Children
- **Housing**: Location, Logement Social
- **Job**: Code `T2A60` (Technicien / Technicienne de laboratoire d'analyse industrielle)
- **Education**: Elémentaire, Collège
- **Health**: Maternité
- **Inclusion**:
  - **Socle Administratif**: Default list (CAF, CPAM, etc.) - _Implicitly selected_
  - **Affinités**: Entraide / Bénévolat
  - **Autres Besoins**: Accompagnement insertion pro (Apprendre français)
- **Weights**:
  - Emploi: 100
  - Logement: 100 (Default)
  - Education: 100 (Default)
  - Santé: 100 (Default)
  - Inclusion: 50
  - Mobilité: 50

## 2. Scoring Components Calculation

The algorithm calculates a score between 0 and 1 for each category for every candidate commune within 50km of Marseille.

### A. Emploi (Weight: 100)

- **Job Market Tension (`met_scaled`)**:
  - Calculates the ratio of job offers (`met`) to the working-age population (`pop_be`) in the Bassin de Vie.
  - Normalized using `QuantileTransformer` (0-1 scale).
- **Job Match (`met_match_adult1_scaled`)**:
  - Checks if the commune's top job families (`be_codfap_top`) include `T2A60`.
  - If present, score is high; otherwise 0.
- **Training Match (`form_match_adult1_scaled`)**:
  - Checks for training centers offering relevant formations. (None specified in demo 3, so likely 0 or irrelevant).

**Category Score**: Weighted average of the above metrics.

### B. Logement (Weight: 100)

- **Social Housing Vacancy (`log_soc_inoc_scaled`)**:
  - Since preference is "Logement Social", it looks at the ratio of unoccupied social housing (`log_soc_inoccupes`) to total social housing (`log_soc_total`).
  - Higher ratio = Higher score (more availability).

### C. Education (Weight: 100)

- **School Structures (`edu_structures_scaled`)**:
  - Checks for presence of **Elémentaire** and **Collège**.
  - If both are present: Score = 1.0.
  - If one is present: Score = 0.5.
  - If neither: Score = 0.0.
- **Closure Risk (`edu_classes_ferm_scaled`)**:
  - Considers the ratio of classes at risk of closure. Lower risk is better (but metric might be inverted or normalized such that "good" is 1).

### D. Santé (Weight: 100)

- **Maternity Presence (`sante_structures_scaled`)**:
  - Checks if a **Maternité** is present in the commune.
  - Present = 1.0, Absent = 0.0.

### E. Inclusion (Weight: 50)

- **Socle Administratif (`inc_socle_admin_score`)**:
  - Checks for presence of default services (CAF, Pôle Emploi/France Travail, Mairie, etc.).
  - Score = (Number of present services) / (Total requested services).
- **Lien Social (`inc_lien_social_score`)**:
  - Calculates density of "Core" associations (Social, Entraide, etc.) per 1000 inhabitants.
  - Normalized relative to other communes.
- **Affinité (`inc_affinite_score`)**:
  - Calculates density of associations matching "Entraide / Bénévolat".
  - Normalized relative to other communes.
- **Population (`inc_population_scaled`)**:
  - Population size normalized to favor larger communes (urban centers).
- **Global Inclusion Score**: Average of Socle, Lien Social, Affinité, and Population.

### F. Mobilité (Weight: 50)

- **Distance (`mob_dist_scaled`)**:
  - Linear score based on distance from Marseille (0km = 1.0, 50km = 0.0).
- **EPCI (`mob_epci_scaled`)**:
  - 1.0 if in the same EPCI (Métropole d'Aix-Marseille-Provence), 0.0 otherwise.
- **Gare (`mob_gare_scaled`)**:
  - 1.0 if the commune has a train station (Source: Odace), 0.0 otherwise.

## 3. Aggregation (Bassin de Vie Level)

If the view is "Bassins de Vie":

1.  Scores are calculated for each **Commune**.
2.  Commune scores are aggregated to the **Bassin de Vie** level using a population-weighted average.
    - _Example_: If a BV has Commune A (Pop 1000, Score 0.8) and Commune B (Pop 500, Score 0.4):
    - BV Score = (1000 x 0.8 + 500 x 0.4) / 1500 = (800 + 200) / 1500 = 0.66.

## 4. Final Weighted Score

The final score for a candidate (Commune or BV) is the weighted average of category scores:

$$
\text{Final Score} = \frac{100 \times \text{Emploi} + 100 \times \text{Logement} + 100 \times \text{Education} + 100 \times \text{Santé} + 50 \times \text{Inclusion} + 50 \times \text{Mobilité}}{100 + 100 + 100 + 100 + 50 + 50}
$$

$$
\text{Final Score} = \frac{100 \times E + 100 \times L + 100 \times Ed + 100 \times S + 50 \times I + 50 \times M}{500}
$$

## 5. Example: Aubagne (Hypothetical)

Let's assume Aubagne is a candidate commune.

1.  **Emploi**: High demand for lab techs -> Score **0.9**
2.  **Logement**: Moderate social housing availability -> Score **0.6**
3.  **Education**: Has both schools -> Score **1.0**
4.  **Santé**: Has a maternity -> Score **1.0**
5.  **Inclusion**: Good services and associations -> Score **0.8**
6.  **Mobilité**: ~15km from Marseille, same EPCI, has a **Gare** -> Score **0.90** (Avg of 0.7, 1.0, 1.0)

$$
\text{Score} = \frac{100(0.9) + 100(0.6) + 100(1.0) + 100(1.0) + 50(0.8) + 50(0.90)}{500}
$$

$$
\text{Score} = \frac{90 + 60 + 100 + 100 + 40 + 45}{500} = \frac{435.0}{500} = \mathbf{0.87}
$$

Aubagne would likely be a top result.
