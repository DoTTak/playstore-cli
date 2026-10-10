# playstore-cli demo: the README GIF and a video for social posts. Run from the repository root:
#   PATH="$PWD/.venv/bin:$PATH" vhs .github/vhs/playstore-cli.tape
# Uses the signed-in account in ~/.config/playstore-cli. Downloads go to a temporary directory,
# or to $DEMO_DIR when it is set.

Output .github/playstore-cli.gif
Output .github/vhs/playstore-cli.mp4

Require playstore-cli
Require zsh

Set Shell "zsh"
Set FontFamily "D2Coding"
Set FontSize 30
Set LineHeight 1.1
Set Width 1920
Set Height 1080
Set Framerate 30
Set Padding 36
Set Margin 56
Set MarginFill ".github/vhs/background.png"
Set WindowBar Colorful
Set BorderRadius 12
Set TypingSpeed 40ms
Set CursorBlink false

# Colors from .github/banner.jpg
Set Theme { "name": "playstore-cli", "background": "#030F15", "foreground": "#D5DEE8", "cursor": "#15FFD0", "selection": "#1A3A44", "black": "#0B1D24", "red": "#FF6B6B", "green": "#3DDC84", "yellow": "#F5D76E", "blue": "#56D8FF", "purple": "#B9A2FF", "cyan": "#15FFD0", "white": "#C9D3E0", "brightBlack": "#4A5D6B", "brightRed": "#FF8A8A", "brightGreen": "#5EF2A0", "brightYellow": "#FFE08A", "brightBlue": "#8AE4FF", "brightPurple": "#D0C0FF", "brightCyan": "#7DFFE4", "brightWhite": "#F4F8FF" }

Hide
Type `source .github/vhs/setup.zsh && cd "${DEMO_DIR:-$(mktemp -d)}" && rm -rf downloads && intro`
Enter
Sleep 1s
Show

# The terminal opens with the logo.
Sleep 3s

Hide
Type "caption SEARCH 'Find apps by name or keyword'"
Enter
Sleep 300ms
Show
Sleep 600ms
Type "playstore-cli search youtube -n 3"
Sleep 400ms
Enter
Sleep 3.5s

Hide
Type "scene DETAILS 'Version, installs and permissions'"
Enter
Sleep 300ms
Show
Sleep 600ms
Type "playstore-cli details com.google.android.youtube | head -15"
Sleep 400ms
Enter
Sleep 4s

Hide
Type "scene CERTIFICATES 'Official signing certificates, checked against the APK Play serves'"
Enter
Sleep 300ms
Show
Sleep 600ms
Type "playstore-cli cert com.google.android.youtube --apk"
Sleep 400ms
Enter
Wait+Screen@30s /matches details/
Sleep 5s

Hide
Type "scene DOWNLOAD 'Base and split APKs, verified against Play'"
Enter
Sleep 300ms
Show
Sleep 600ms
Type "playstore-cli download com.google.android.youtube"
Sleep 400ms
Enter
Wait+Screen@60s /Install:/
Sleep 4s

Hide
Type "outro"
Enter
Sleep 500ms
Show
Sleep 4.5s
