# Fonctions partagées par les lanceurs Spirula ; à sourcer, pas à exécuter.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/shell_helpers.sh"

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

# Résout "auto" vers l'index du premier GPU ok : discrete, puis integrated,
# sinon auto. Les colonnes sont : no, nom, type, VRAM (2 champs), uuid, état.
resolve_spirula_device() {
  local bin="$1" requested="$2" output kind index
  if [[ "$requested" != auto ]]; then
    printf '%s\n' "$requested"
    return
  fi
  output="$("$bin" sam devices 2>&1)" || {
    printf 'auto\n'
    return
  }
  for kind in discrete integrated; do
    index="$(awk -v kind="$kind" \
      '$1 ~ /^[0-9]+$/ && $NF == "ok" && $(NF-4) == kind {print $1; exit}' <<<"$output")"
    if [[ -n "$index" ]]; then
      printf '%s\n' "$index"
      return
    fi
  done
  printf 'auto\n'
}
