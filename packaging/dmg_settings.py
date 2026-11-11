# dmgbuild settings for the Mac installer window. build_mac.sh runs dmgbuild from the repo folder and passes in:
#   app  - path to the signed Blendbook.app
# Icon positions must match packaging/dmg_background.py.
import os

application = defines["app"]  # noqa: F821 (dmgbuild provides `defines`)
here = "packaging"  # dmgbuild runs this file with exec, so there is no __file__

format = "UDZO"
filesystem = "HFS+"
files = [application]
symlinks = {"Applications": "/Applications"}
icon = os.path.join(here, "Blendbook.icns")  # the mounted disk's own icon

background = os.path.join(here, "dmg-background.tiff")
window_rect = ((200, 160), (640, 400))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
include_icon_view_settings = True

icon_size = 112
text_size = 13
arrange_by = None
label_pos = "bottom"
icon_locations = {
    os.path.basename(application): (170, 196),
    "Applications": (470, 196),
}
