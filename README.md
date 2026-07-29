# IonoCast

**SO(2) equivariant spherical neural operators for global ionospheric TEC forecasting.**

🌐 [ionocast.com](https://ionocast.com) &nbsp;·&nbsp; 📄 Paper coming soon

---

## What this is

IonoCast forecasts global ionospheric Total Electron Content (TEC) 1–6
hours ahead. TEC is the vertically integrated electron density of the
ionosphere; it drives GNSS positioning error, HF communication
blackouts, and satellite drag, with the largest disruptions during
geomagnetic storms.

Global TEC maps are functions on a sphere. Most deep-learning
forecasters flatten them into rectangular images, which distorts the
geometry near the poles and tears the longitude seam. IonoCast instead
does all spatial reasoning natively on the sphere using a **Spherical
Fourier Neural Operator (SFNO)**, and forecasts the deviation from the
IRI-2016 climatology rather than raw TEC.

The core idea: the right symmetry for an Earth-fixed, Sun-driven field
is **SO(2)** (rotation about Earth's axis), not the full rotation group
SO(3) used by general-purpose spherical CNNs. Building in exactly this
symmetry (no more, no less) gives a model that is both physically
correct and more expressive than the SO(3) alternative.

---

## Highlights

- **Spherical, not flat.** Forecasts the TEC field in the spherical
  harmonic domain; no polar distortion, no seam.
- **Correct symmetry.** SO(2)-equivariant spectral filters, derived and
  proven to be exactly the right equivariant class for this problem.
- **Physics-residual target.** Predicts the residual against the
  IRI-2016 reference ionosphere, so the network learns the storm-time
  deviations that physics models miss.
- **Everything derived, not tuned.** The spectral bandwidth, the
  regularizer, the loss weighting, and the anti-ringing window all come
  from the mathematics rather than a hyperparameter sweep.
- **Verified equivariance.** A built-in metric confirms the trained
  model respects the intended symmetry.

---

## Status

🚧 **Active development.** The architecture and the mathematical
framework are settled; training and evaluation are in progress.

A full write-up with derivations, proofs, and benchmark results against
persistence, IRI climatology, the operational CODE C1PG/C2PG products,
and a flat-CNN ablation is **on the way**. Check back here and at
[ionocast.com](https://ionocast.com) for the preprint.

---

## Roadmap

- [x] Mathematical framework (SO(2) equivariance, spectral bandwidth,
  Sobolev regularization, Gibbs window)
- [x] Data pipeline (IONEX + IRI residual + solar-wind conditioning)
- [ ] Full training run and baseline comparison
- [ ] Interactive forecast globe on
  [ionocast.com](https://ionocast.com)
- [ ] Preprint release
- [ ] Vector-valued (gauge-equivariant) extension for magnetic-field
  data

---

## Team

- **Saksham Hassanandani** — mathematical framework
  (University of Colorado Boulder)
- **Hayden Samala** — data pipeline (UCLA)
- **Falisha Amir** — model implementation (University of Washington)

---

## Citation

A citable preprint is coming soon. If you would like to reference this
work before then, please link to
[github.com/hsamala688/ionocast](https://github.com/hsamala688/ionocast)
or [ionocast.com](https://ionocast.com).

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.
