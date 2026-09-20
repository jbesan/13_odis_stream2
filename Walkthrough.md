# Walkthrough — Analyse Avancée terminale

## Décision

L’Analyse Avancée est désormais une tâche en arrière-plan indépendante du dialog.
Le dialog ne s’ouvre qu’après la fin de l’analyse et affiche uniquement un rapport
déjà disponible.

## Flux utilisateur

1. Le clic initial lance l’analyse et laisse le dialog fermé.
2. Le bouton passe à l’état `En cours...` et le toast confirme le lancement.
3. Le fragment du bouton détecte la fin, fusionne le résultat et affiche un toast
   de disponibilité.
4. Le bouton devient `Consulter l'analyse`.
5. Un clic explicite demande un rerun d’application ; le dispatch racine ouvre
   alors le dialog avec le rapport terminé.

Une fin d’analyse ne déclenche plus de rerun global. Un dialog « En savoir plus »
ouvert pendant l’analyse n’est donc plus fermé par sa complétion.

## Vérifications

- Tests UI ciblés (actions, dialogs, état exclusif) : 38 tests passés.
- `tests/unit` et `tests/integration` hors test de caption préexistant :
  519 tests passés, 1 ignoré.
- `tests/e2e` : 31 tests passés.

## Correctif du cycle de vie des dialogs

Les boutons rendus par des fragments (`En savoir plus`, export PDF et partage)
ne créent plus directement un `st.dialog` depuis leur fragment auto-reruné. Ils
enregistrent une demande exclusive dans l'état de session, déclenchent un rerun
d'application, puis le dispatcher racine ouvre le dialog exactement une fois.
Le même état exclusif est utilisé pour « À propos », l'Analyse Avancée et le
CCAS. Les callbacks de fermeture effacent la demande afin qu'un dialog évincé
ne reste pas actif invisiblement.

Le comportement protège notamment contre l'éviction d'un dialog descendant
lors du prochain `run_every=2.0` de son fragment parent, qui provoquait ensuite
`_assert_first_dialog_to_be_opened` lorsque l'utilisateur ouvrait un second
dialog.
