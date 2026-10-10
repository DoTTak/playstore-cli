# Shell setup for playstore-cli.tape: prompt, command-line colors, the logo and scene titles.
# Colors follow .github/banner.jpg.

export LANG=en_US.UTF-8 COLORTERM=truecolor

# r;g;b
mint='21;255;208'     # "-cli", subcommands
cyan='86;216;255'     # options, links
light='244;248;255'   # "playstore"
soft='201;211;242'    # tagline
muted='122;140;160'
ink='3;15;21'         # terminal background

fg() { print -rn -- $'\e[38;2;'$1'm' }
reset=$'\e[0m'

PROMPT='%F{#15FFD0}$%f '

# Color the command line as it is typed: the command, its subcommand and its options.
_highlight() {
  region_highlight=()
  if [[ $BUFFER == '#'* ]]; then
    region_highlight=("0 $#BUFFER fg=#7A8CA0")
    return
  fi
  local pos=0 n=0 w
  for w in ${(s: :)BUFFER}; do
    case $n:$w in
      0:*)       region_highlight+=("$pos $((pos + $#w)) fg=#F4F8FF,bold") ;;
      1:[a-z]*)  region_highlight+=("$pos $((pos + $#w)) fg=#15FFD0") ;;
      *:-*)      region_highlight+=("$pos $((pos + $#w)) fg=#56D8FF") ;;
      *:'|')     region_highlight+=("$pos $((pos + $#w)) fg=#7A8CA0") ;;
    esac
    (( pos += $#w + 1, n++ ))
  done
}
zle -N zle-line-pre-redraw _highlight

# "playstore-cli" in the figlet font ANSI Shadow
_play=(
  '██████╗ ██╗      █████╗ ██╗   ██╗███████╗████████╗ ██████╗ ██████╗ ███████╗'
  '██╔══██╗██║     ██╔══██╗╚██╗ ██╔╝██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗██╔════╝'
  '██████╔╝██║     ███████║ ╚████╔╝ ███████╗   ██║   ██║   ██║██████╔╝█████╗  '
  '██╔═══╝ ██║     ██╔══██║  ╚██╔╝  ╚════██║   ██║   ██║   ██║██╔══██╗██╔══╝  '
  '██║     ███████╗██║  ██║   ██║   ███████║   ██║   ╚██████╔╝██║  ██║███████╗'
  '╚═╝     ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚══════╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚══════╝'
)
_cli=(
  '       ██████╗██╗     ██╗'
  '      ██╔════╝██║     ██║'
  '█████╗██║     ██║     ██║'
  '╚════╝██║     ██║     ██║'
  '      ╚██████╗███████╗██║'
  '       ╚═════╝╚══════╝╚═╝'
)
# One color per row, top to bottom: white for "playstore", mint into cyan for "-cli".
_play_face=('255;255;255' '246;249;253' '235;241;248' '222;231;241' '208;219;233' '194;207;224')
_cli_face=('43;255;210' '33;250;218' '25;242;228' '22;232;238' '24;222;246' '28;212;252')

# A logo row: blocks in the row's color, the box-drawing shadow in a darker one.
_paint() {  # text face shadow
  local face=$'\e[38;2;'$2'm' shadow=$'\e[38;2;'$3'm'
  print -rn -- $shadow${1//█/$face█$shadow}
}

logo() {  # [indent]
  local i
  for i in {1..6}; do
    printf '%*s' ${1:-0} ''
    _paint $_play[i] $_play_face[i] '62;80;99'
    _paint $_cli[i] $_cli_face[i] '9;122;110'
    print -r -- $reset
  done
}

# The screen the terminal opens with.
intro() {
  clear
  print
  logo
  print
  print -r -- "$(fg $soft)Browse, download and inspect Android apps"
  print -r -- "from Google Play Store, right from your terminal.$reset"
  print
  local f
  for f in search details reviews 'top charts' certificates download; do
    print -rn -- "$(fg $mint)●$reset $(fg $muted)$f$reset   "
  done
  print '\n'
}

_badge() {  # name description
  print -r -- $'\e[1;48;2;'$mint$'m\e[38;2;'$ink'm '$1' '$reset"  $(fg $soft)$2$reset"
  print
}

# Title the next scene in place of the line that called this, below the intro.
caption() { print -n '\e[1A\e[2K'; _badge "$@" }

# Clear the screen and title the next scene.
scene() { clear; print; _badge "$@" }

# The closing card, centered; it keeps the screen until the recording ends.
outro() {
  local top=$(( (LINES - 10) / 2 )) left=$(( (COLUMNS - 100) / 2 ))
  clear
  print -n '\e[?25l'
  repeat $top print
  logo $left
  print
  printf '%*s' $left ''; print -r -- "$(fg $muted)\$$reset $(fg $light)pip install playstore-cli$reset"
  printf '%*s' $left ''; print -r -- "$(fg $muted)★$reset $(fg $cyan)github.com/DoTTak/playstore-cli$reset"
  sleep 60
}
