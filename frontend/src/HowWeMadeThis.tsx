import React from 'react';
import './HowWeMadeThis.css';

// "How We Made This" — a scaffold of the data pipeline.
//
// Layout: a two-column grid. The RIGHT column is the pipeline flow (one card per
// stage, top to bottom). The LEFT column has an aligned note box per stage where
// you fill in the *choices* made at that stage. A full-width section at the
// bottom is left empty for challenges & mistakes.
//
// Structure is data-driven (edit the arrays); copy is terse placeholder text.

// `note` is YOUR fill-in for the left column: the choices/reasoning for this
// stage. Leave it out to show the dashed "fill in…" placeholder. It's a
// ReactNode, so a plain string works, or JSX (e.g. a <ul> of bullet points).
type Stage = { cmd?: string; title: string; desc: string; note?: React.ReactNode };
type LayerId = 'bronze' | 'silver' | 'gold';
type Layer = { id: LayerId; label: string; tagline: string; stages: Stage[] };

// --- Ingestion: the raw sources that fan into the pipeline ---
const SOURCES: Stage[] = [
  { title: 'CDDIS IONEX', 
    desc: 'Global TEC maps (authenticated via ~/.netrc).',
    note: `We chose this source as it is a compilation from NASA's CDDIS Archive which itself derives its information 
    from a variety of sources. This coverage allowed us the best chance to collect all available TEC data which is ultimately 
    the metric we predicted for and displayed.`,
  },

  { title: 'SPDF OMNI HRO - 5 MIN', 
    desc: 'Solar-wind drivers (anonymous).',
    note: `This data comes from a collection of satellites (ACE, Wind, IMP 8, Geotail). Some of these satellites (though not all)
    are sitting at Lagrange point 1, providing key solar driver metrics: B-field Magnitude (In both Y and Z directions), 
    Flow Speed of the solar winds, Proton Density from said solar winds. `
   },
  { title: 'GFZ indices', 
    desc: 'Kp / geomagnetic activity (anonymous).',
    note: `This provides the canonical versions of both Kp Index as well F10.7 which was used to calculate and then assmebling 
    our Gauss-Legendre grid dataset which we ultimately used for our S2CNN model training.`, 
  },
];

// --- Processing: medallion layers, one card per CLI stage ---
const LAYERS: Layer[] = [
  {
    id: 'bronze',
    label: 'Bronze',
    tagline: 'Raw, as-downloaded',
    stages: [
      { cmd: 'swp-data extract', 
        title: 'Extract', 
        desc: 'Download IONEX + OMNI HRO + GFZ; write raw files and manifests.', 
        note: `This stage allows for the correct extraction form these three different data sources, compiling all the neccesary data in
        their raw form. IONEX CDDIS access requires a NASA login, while the other two sources can be collected through simple API pings. 
        The formatting, however, across the different data sources had to be reconciled along with several different data gaps within the 
        sources as well.`
      },
    ],
  },
  {
    id: 'silver',
    label: 'Silver',
    tagline: 'Cleaned & aligned',
    stages: [
      { cmd: 'swp-data parse', title: 'Parse drivers', desc: 'F10.7 daily and Kp 3-hourly tables.' },
      { cmd: 'swp-data interpolate', title: 'Interpolate TEC', desc: 'IONEX → Gauss–Legendre 23×45 sphere (+ grid.npz).' },
      { cmd: 'swp-data assemble iri', title: 'IRI baseline', desc: 'Climatology baseline field. Slowest stage.' },
      { cmd: 'swp-data assemble dtec', title: 'Residual dTEC', desc: 'residual = tec − iri.' },
      { cmd: 'swp-data assemble omni', title: 'Align drivers', desc: 'Drivers aligned to dTEC frame timestamps.' },
    ],
  },
  {
    id: 'gold',
    label: 'Gold',
    tagline: 'Model-ready',
    stages: [
      { cmd: 'swp-data assemble windows', title: 'Training windows', desc: 'Normalized input/target windows + metadata.json.' },
    ],
  },
];

// --- Model: what learns on the gold windows ---
const MODEL: Stage = {
  title: 'SFNO model (s2cc)',
  desc: 'Spherical Fourier Neural Operator trained on the windows; predicts residual dTEC, then + IRI baseline → absolute predicted TEC. TODO: architecture, training, metrics.',
};

// --- Serving: how a map gets from model/observation onto the globe ---
const SERVING: Stage[] = [
  { title: 'Export native maps', desc: '71×73 hourly TEC grids per year (.npz).' },
  { title: 'Build day blobs', desc: '24 hourly maps → little-endian float16, gzipped, one file per UT day.' },
  { title: 'Upload to Cloudflare R2', desc: 'actual/ at the bucket root, predicted/ under a key prefix.' },
  { title: 'Worker serves /tec/*', desc: 'Same-origin, gzip content-encoding, immutable edge cache.' },
  { title: 'Globe renders', desc: 'float16 → HalfFloat DataTexture; shown hour tracks the sun as the globe spins.' },
];

// Right column: one pipeline stage.
const StageCard: React.FC<{ stage: Stage; accent?: LayerId }> = ({ stage, accent }) => (
  <div className={`hw-card${accent ? ` hw-card--${accent}` : ''}`}>
    <h4 className="hw-card__title">{stage.title}</h4>
    {stage.cmd && <code className="hw-card__cmd">{stage.cmd}</code>}
    <p className="hw-card__desc">{stage.desc}</p>
  </div>
);

// A pipeline row: left note (fill in the choice) + right stage card, aligned.
const Row: React.FC<{ stage: Stage; accent?: LayerId }> = ({ stage, accent }) => (
  <>
    <div className="hw-note">
      <span className="hw-note__label">{stage.title}</span>
      {stage.note ? (
        <div className="hw-note__body">{stage.note}</div>
      ) : (
        <p className="hw-note__placeholder">Fill in the choices made at this stage.</p>
      )}
    </div>
    <StageCard stage={stage} accent={accent} />
  </>
);

const SectionHead: React.FC<{ num: string; title: React.ReactNode; sub: string }> = ({ num, title, sub }) => (
  <div className="hw-sectionhead">
    <span className="hw-sectionhead__num">{num}</span>
    <h2 className="hw-sectionhead__title">{title}</h2>
    <p className="hw-sectionhead__sub">{sub}</p>
  </div>
);

export const HowWeMadeThis: React.FC = () => {
  return (
    <main className="hw">
      <header className="hw-hero">
        <h1 className="hw-hero__title">How We Made This</h1>
      </header>

      {/* Column key */}
      <div className="hw-colkey" aria-hidden="true">
        <span className="hw-colkey__left">Choices &amp; reasoning</span>
        <span className="hw-colkey__right">Pipeline flow</span>
      </div>

      {/* Two-column pipeline: left notes align with right stage cards */}
      <div className="hw-pipeline">
        {/* 01 Ingestion */}
        <SectionHead num="01" title="Ingestion" sub="Three raw space-weather sources." />
        {SOURCES.map((s) => <Row key={s.title} stage={s} />)}

        {/* 02 Processing (medallion) */}
        <SectionHead num="02" title="Processing" sub="Medallion layers — each stage is a CLI subcommand." />
        {LAYERS.map((layer) => (
          <React.Fragment key={layer.id}>
            <div className={`hw-layerhead hw-layer--${layer.id}`}>
              <span className="hw-layerhead__dot" />
              <span className="hw-layerhead__label">{layer.label}</span>
              <span className="hw-layerhead__tagline">{layer.tagline}</span>
            </div>
            {layer.stages.map((s) => <Row key={s.title} stage={s} accent={layer.id} />)}
          </React.Fragment>
        ))}

        {/* 03 Model */}
        <SectionHead num="03" title="Model" sub="Learns the residual field on the gold windows." />
        <Row stage={MODEL} />

        {/* 04 Serving & visualization */}
        <SectionHead num="04" title="Serving & Visualization" sub="From numpy arrays to maps on the globe." />
        {SERVING.map((s) => <Row key={s.title} stage={s} />)}
      </div>

      {/* 05 Challenges & mistakes — intentionally empty, fill in later */}
      <section className="hw-challenges">
        <SectionHead num="05" title="Challenges & Mistakes" sub="What went wrong and what we learned along the way." />
        <div className="hw-challenges__box">
          <p className="hw-note__placeholder">
            {/* TODO: document challenges, dead ends, and mistakes here */}
            Fill in the challenges and mistakes made along the way.
          </p>
        </div>
      </section>
    </main>
  );
};
