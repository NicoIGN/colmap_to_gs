# Tests

Tous les tests Python sont ici, en miroir de l'arborescence de `tools/` :
`tests/<domaine>/test_X.py` teste `tools/<domaine>/X.py` (ou le module du même domaine).
Les tests `environment/` et `install/` suivent le même principe pour leurs dossiers.

## Lancer

Depuis la racine du dépôt, venv `~/.venvs/colmap_to_gs` activé :

```bash
PYTHON_BIN=python tests/run_all.sh                 # tous les tests, séquentiel
PYTHON_BIN=python tests/run_all.sh --phase masks   # une phase (sous-dossier de tests/)
python tests/libs/test_pipeline.py -k <nom>        # un test ciblé
```

Les chemins sont résolus depuis le fichier de test (`parents[N]`) ; ne pas les remplacer par des chemins absolus.
