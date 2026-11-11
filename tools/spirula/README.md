# Installer Spirula Studio et SAM 3

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

## 1. Objectif

Installer Spirula Studio avec le backend Vulkan/SAM 3 utilisé pour générer les
[masques natifs du pipeline](../segmentation_masks/README.md).

Exemple d’invocation :

```bash
bash /chemin/vers/colmap_to_gs/tools/spirula/install_spirula.sh
```

## 2. Entrées et sorties

Sortie : `spirula-studio/build/spirula` sur macOS,
`spirula-studio/build_vulkan/spirula` sur Linux. Le lanceur affiche la commande
`export SPIRULA_BIN=...` et `export TRAIN_MASKS_SPIRULA_MODEL=...` à utiliser
avec les scripts du pipeline. Pour le script autonome de masques, reporter le
chemin du modèle affiché dans `SAM_MODEL`.

Options de l'installateur :

- `--repo-dir PATH` : dossier des sources ; défaut `<colmap_to_gs>/../spirula-studio`.
- `--repo-url URL` : dépôt à cloner ; défaut, le dépôt officiel.
- `--clean` : met à jour et recompile même si l'exécutable existe déjà.
- `-- [OPTIONS CMAKE]` : transmet les options CMake ; elles ne s'appliquent qu'avec `--clean` si un exécutable existe.
- `-h`, `--help` : affiche l'aide.


## 3. Configuration et utilisation


Depuis n'importe quel dossier :

```bash
bash /chemin/vers/colmap_to_gs/tools/spirula/install_spirula.sh
```

Si l'exécutable existe déjà, le script le réutilise **sans mettre à jour le
dépôt ni recompiler**. Son existence ne garantit pas qu'il inclut SAM ou
`--mask-mode`, nécessaires au backend de masques : vérifier sa version ou
utiliser `--clean` pour recompiler.

Pour mettre à jour et recompiler proprement :

```bash
bash tools/spirula/install_spirula.sh --clean
```

Cette option exécute la cible CMake `clean` du dossier de build existant puis
recompile ; elle ne supprime pas le modèle. Le dossier `models/` produit par
l'installateur est exclu du contrôle des modifications Git ; les autres
modifications locales empêchent toujours la mise à jour.

Pour choisir un autre emplacement, un fork ou des options CMake :

```bash
bash tools/spirula/install_spirula.sh \
  --repo-dir /chemin/vers/spirula-studio \
  --repo-url https://github.com/harry7557558/spirula-studio.git \
  -- -DSS_BUILD_GUI=OFF
```

`--repo-url` sert seulement lors du clonage : un checkout existant conserve son
remote de suivi. Les chemins relatifs fournis sont résolus depuis le dossier
de lancement. Les options CMake après `--` nécessitent `--clean` si un
exécutable existe déjà. Le script amont choisit le parallélisme selon la RAM.

## 4. Fonctionnement interne

Le script `install_spirula.sh` récupère les sources de Spirula Studio,
le compile pour le système visé et vérifie que l'exécutable démarre. Il
réutilise une installation existante, sauf si `--clean` est demandé.

Pour le backend SAM 3, le script télécharge aussi le modèle nécessaire. Il
vérifie que le fichier téléchargé semble complet avant de le conserver ; cette
vérification ne garantit pas son intégrité cryptographique. Un modèle présent
mais incomplet n'est pas écrasé automatiquement : le déplacer puis relancer
l'installation.

## 5. Notes et informations complémentaires

Il télécharge également `sam3-q4_0.ggml` (~707 Mio) dans
`spirula-studio/models/` depuis
[`PABannier/sam3.cpp`](https://huggingface.co/PABannier/sam3.cpp).
Le modèle est soumis à la licence Meta SAM 3 : consulter ses conditions avant
utilisation. L'installateur **n'accepte aucune licence à votre place** ;
Spirula peut demander une acceptation séparée selon sa version.

Prérequis de compilation : Git, CMake, Ninja et un compilateur C++ compatible
avec Spirula. Le téléchargement du modèle nécessite `curl` et un accès réseau.
Sur macOS : outils de développement Xcode (`xcode-select --install`) ;
CMake récupère MoltenVK. Sur Linux : SDK Vulkan et dépendances système indiquées
dans les instructions de compilation du projet amont. Le lanceur n'installe
aucun paquet système et n'active pas les décodeurs vidéo brevetés.
