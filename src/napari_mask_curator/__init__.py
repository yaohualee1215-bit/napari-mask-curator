try:
    from ._version import version as __version__
except Exception:
    __version__ = "0.0.0"

from ._widget import make_mask_curation_widget

__all__ = ["make_mask_curation_widget"]
