"""Built-in data exporters.

Importing this module registers every exporter below.  To add a new output
type: create a module here with a class decorated with
``@register("your_kind")`` and import it in this list — nothing else in the
codebase needs to change.
"""

from . import rgb_camera  # noqa: F401  (registers "rgb_camera")
from . import lidar       # noqa: F401  (registers "lidar")
from . import kitti       # noqa: F401  (registers "kitti")
from . import stream      # noqa: F401  (registers "stream")
from . import manifest    # noqa: F401  (registers "manifest")

# future exporters (once implemented):
# from . import semantic_segmentation  # noqa: F401
# from . import depth                  # noqa: F401
# from . import vehicle_state          # noqa: F401
# from . import traffic_light          # noqa: F401
