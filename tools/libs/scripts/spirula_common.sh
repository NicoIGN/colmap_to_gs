# Fonctions partagées par les lanceurs Spirula ; à sourcer, pas à exécuter.

# Exécutable Spirula par défaut, indépendant du dossier courant :
# `spirula` dans le PATH, sinon ../spirula-studio/build/spirula à côté du dépôt.
default_spirula_bin() {
  local repo_root
  repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
  if command -v spirula >/dev/null 2>&1; then
    printf 'spirula\n'
  else
    printf '%s\n' "$(dirname -- "$repo_root")/spirula-studio/build/spirula"
  fi
}
