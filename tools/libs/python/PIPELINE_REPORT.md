# Rapports de durée

Les pipelines images, vidéos et Panoramax, leurs soumissions Slurm et les
maîtres LiDAR écrivent automatiquement **`pipeline_report.json` dans leur
dossier de travail**. Aucun paramètre d'activation n'est nécessaire.
La prévalidation seule (`--validate-only`) ne crée pas de rapport.

Le fichier conserve un historique `runs` : chaque exécution/job a un identifiant
unique, son nom, son parent éventuel, les identifiants Slurm et :

- `started_at`, `finished_at` : dates ISO 8601 UTC ;
- `duration_seconds` : durée de cette exécution ;
- `phases` : début, fin, durée et statut de chaque phase ;
- `status`, `exit_code` : succès ou échec ;
- `statistics` : nombre d'images/blocs/points sparse, tailles et nombres de
  splats, taille/nombre de points LiDAR, lorsque ces artefacts sont disponibles.

Les phases incluent notamment extraction vidéo, sélection des images, masques,
SfM, GPX, découpage, entraînement par bloc, fusion, recalage/enrichissement LiDAR
et nettoyage final selon les options utilisées.
Un parent peut inclure le temps d'un enfant : **ne pas additionner leurs durées**
comme si elles étaient indépendantes. Une phase réutilisant un cache est
enregistrée également, avec le temps réellement consacré à sa vérification.
`history_span_seconds` représente la période couverte par l'ensemble de
l'historique, y compris les pauses entre les exécutions, pas du temps de calcul.

Les mises à jour sont atomiques et protégées par verrou fichier (`flock`) pour
éviter que les jobs Slurm parallèles écrasent les entrées des autres jobs.
La soumission Slurm est rapportée séparément des jobs : sa fin ne signifie
pas que l'entraînement est terminé.

En cas d'erreur normale, le rapport clôture l'exécution et sa dernière phase en
échec. Un arrêt brutal (`SIGKILL`, machine arrêtée) peut laisser `running` et une
date de fin vide ; ce n'est pas présenté comme une exécution terminée.
Les statistiques sont des compteurs d'artefacts, pas des métriques de qualité.
