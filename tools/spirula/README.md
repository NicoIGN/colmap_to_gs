# Installer ou mettre à jour Spirula Studio

Depuis n'importe quel dossier :

```bash
bash /chemin/vers/colmap_to_gs/tools/spirula/install_spirula.sh
```

Le script clone le dépôt officiel dans `../spirula-studio` à côté de
`colmap_to_gs`, ou met à jour la branche courante de ce checkout avec
`git pull --ff-only`. Il refuse les modifications locales et les divergences,
initialise les sous-modules et lance `build_develop.bash` avec le backend Vulkan
et le mode Release. Il vérifie ensuite l'exécutable avec `--version`.

Prérequis : Git, CMake, Ninja et un compilateur C++ compatible avec Spirula.
Sur macOS : outils de développement Xcode (`xcode-select --install`) ;
CMake récupère MoltenVK. Sur Linux : SDK Vulkan et dépendances système indiquées
dans les instructions de compilation du projet amont. Le lanceur n'installe
aucun paquet système et n'active pas les décodeurs vidéo brevetés.

Pour choisir un autre emplacement, un fork ou des options CMake :

```bash
bash tools/spirula/install_spirula.sh \
  --repo-dir /chemin/vers/spirula-studio \
  --repo-url https://github.com/harry7557558/spirula-studio.git \
  -- -DSS_BUILD_GUI=OFF
```

`--repo-url` sert seulement lors du clonage : un checkout existant conserve son
remote de suivi. Les chemins relatifs fournis sont résolus depuis le dossier
de lancement. Le script amont choisit le parallélisme selon la RAM disponible.

Sortie : `spirula-studio/build/spirula` sur macOS,
`spirula-studio/build_vulkan/spirula` sur Linux. Le lanceur affiche la commande
`export SPIRULA_BIN=...` à utiliser avec les scripts du pipeline.
