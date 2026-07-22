"""swp_data: the Space Weather Predictor data pipeline, clean-repo layout.

Stages:
  extract     Stage 1: raw downloads (CDDIS IONEX, SPDF OMNI HRO, GFZ indices)
  parse       Stage 2: parsers + derivation of the F10.7/Kp driver-index tables
  interpolate Stage 3: native IONEX 71x73 -> Gauss-Legendre 23x45
  assemble    Stage 4: IRI baseline, dTEC residual, driver alignment, windows
  dataset     Stage 5: PyTorch Dataset over the windowed output

Driver-index sources are consolidated on GFZ Potsdam (the Kp producer): its
combined Kp/ap/Ap/SN/F10.7 file is the single origin for both Kp and observed
F10.7. CelesTrak is retired; the OMNI2 download is dropped (its F10.7 is
adjusted-to-1AU, which the IRI baseline must not use, and its June 2006 Kp
contradicts the definitive GFZ record).
"""
