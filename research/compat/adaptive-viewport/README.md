# Adaptive viewport

The primary display safe area is encoded as `RA3_SAFE_VIEWPORT=screenWidthxscreenHeight:left,top,right,bottom`. Edges are exclusive Wine raw coordinates, with an explicit Retina2 unit contract. Actual Metal backing pixels are tracked separately.

Apply the fixed patch sequence before these adaptive deltas. See[BUILDING](../../../docs/BUILDING.md) and the[root README](../../../README.md).

`run_checks.py` runs game-free offline tests; actual Wine-route checks accept `--wine-source` for a separately prepared checkout. Physical validation currently covers a2x notched primary display only. Display topology changes during play require restart.
