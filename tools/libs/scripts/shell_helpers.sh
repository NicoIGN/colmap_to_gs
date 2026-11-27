# Helpers bash partagés par les lanceurs ; à sourcer, pas à exécuter.

die() { printf '[ERREUR] %s\n' "$*" >&2; exit 1; }

# realpath -e/-m n'existe pas sur macOS : Python est portable.
resolve_path() { python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"; }

need_arg() {
  [[ $# -ge 2 ]] || die "Valeur manquante pour $1"
  [[ -n "${2:-}" ]] || die "Valeur vide pour $1"
}

is_true() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|y|on) return 0 ;;
    0|false|no|n|off|"") return 1 ;;
    *) die "Booléen invalide: $1" ;;
  esac
}

# Les paramètres GRID_* ne servent qu'au mode GRID (--grid) : refus si la configuration en contient.
reject_grid_params() {
  local config="$1" names
  [[ -f "$config" ]] || return 0
  names="$(grep -oE '^[[:space:]]*(export[[:space:]]+)?GRID_[A-Za-z0-9_]*=' "$config" |
    sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=$//' | sort -u | paste -sd, -)" || true
  [[ -z "$names" ]] ||
    die "Paramètres GRID ($names) dans $config : lancer avec --grid (launch_panoramax_to_3dgs.sh ou launch_images_to_3dgs.sh) ou retirer ces paramètres."
}

is_positive_integer() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
is_nonnegative_integer() { [[ "$1" =~ ^[0-9]+$ ]]; }
is_number() { [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]; }
