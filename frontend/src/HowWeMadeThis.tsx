import React from 'react';
import './HowWeMadeThis.css';

// "How We Made This" — one unified flow of the data pipeline.
//
// Layout: a single vertical flow threaded onto a connecting spine. Each stage is
// one node that combines the pipeline step (what it does) with the choices and
// reasoning behind it (why), so the "what" and the "why" read together instead
// of in two separate columns. Sections group the flow (ingestion, processing,
// model, serving) and the last section collects the challenges & mistakes.
//
// Structure is data-driven (edit the arrays).

// `note` is the "why / choices" for a stage. Leave it out (or empty) and the
// node renders the "what it does" half alone — no empty section. It's a
// ReactNode, so a plain string works, or JSX (e.g. a <ul> of bullet points).
type Stage = { cmd?: string; title: string; desc: string; note?: React.ReactNode };
type LayerId = 'bronze' | 'silver' | 'gold';
type Layer = { id: LayerId; label: string; tagline: string; stages: Stage[] };

// --- Ingestion: the raw sources that fan into the pipeline ---
const SOURCES: Stage[] = [
  { title: 'CDDIS IONEX', 
    desc: 'Global TEC maps',
    note: `We chose this source as it is a compilation from NASA's CDDIS Archive which itself derives its information 
    from a variety of sources. This coverage allowed us the best chance to collect all available TEC data which is ultimately 
    the metric we predicted for and displayed.`,
  },

  { title: 'SPDF OMNI HRO - 5 MIN', 
    desc: 'Solar-wind drivers',
    note: `This data comes from a collection of satellites (ACE, Wind, IMP 8, Geotail). Some of these satellites (though not all)
    are sitting at Lagrange point 1, providing key solar driver metrics: B-field Magnitude (In both Y and Z directions), 
    Flow Speed of the solar winds, Proton Density from said solar winds. `
   },
  { title: 'GFZ indices', 
    desc: 'Kp / geomagnetic activity',
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
        note: (
          <>
            <p>
              This stage allows for the correct extraction from these three different data sources, compiling all the
              neccesary data in their raw form. The formatting, however, across the different data sources had to be
              reconciled along with a major data gap coming from IONEX, unfortunately with many of the missing days
              falling within our training years:
            </p>
            <ul>
              <li>2020-342: a single isolated day (Dec 7, 2020)</li>
              <li>2022-331 → 2022-365: 35 contiguous days (Nov 27 – Dec 31, 2022)</li>
              <li>2023-001 → 2023-218: 218 contiguous days (Jan 1 – Aug 6, 2023)</li>
            </ul>
          </>
        ),
      },
    ],
  },
  {
    id: 'silver',
    label: 'Silver',
    tagline: 'Cleaned & aligned',
    stages: [
      { cmd: 'swp-data parse', 
        title: 'Parse drivers', 
        desc: 'F10.7 daily and Kp 3-hourly tables.', 
        note: `This stage first takes the Z (Unix LZW) / .gz (gzip) / .asc|.txt (plain) and coverts it into a plain text stream. It then 
          takes the IONEX v1.0 stream and turns it into a list of (datetime, ndarray) TEC maps. After that, it takes the OMNI 5-min HRO 
          stream and converts it into a DataFrame of the five driver channels. It then takes the GFZ combined index file and extracts 
          the daily Kp index and F10.7obs records. Finally, it takes the parse_gfz output and extracts teh remaining the two driver-index parquet`
      },
      { cmd: 'swp-data interpolate', 
        title: 'Interpolate TEC', 
        desc: 'IONEX → Gauss–Legendre 23×45 sphere (+ grid.npz).',
        note: `This stage resamples every native IONEX TEC map (a 71×73 lat/lon grid) onto the Gauss-Legendre
        23×45 sphere that our spherical model expects. The grid is the model's native coordinate system, so doing
        the interpolation once here keeps every downstream stage on identical coordinates. The latitudes are
        Gauss-Legendre nodes rather than an even spacing (they sit interior to the poles, ±84.14°, so no cell ever
        lands exactly on a singular pole), and the longitudes are equiangular from 0-360°. To interpolate cleanly we
        convert longitudes to the 0-360° convention, wrap-pad across the 0/360° seam so the map stays continuous
        there, and re-sort latitudes into ascending order for SciPy's interpolator. We also collapse the duplicate
        maps IONEX produces at day boundaries (the 24:00 map of one day is the same instant as the 00:00 map of the
        next), keeping the earlier copy and checking the two solutions agree to within ~5% before dropping one. The
        result is written as one .npz per year, plus a single grid.npz recording the target lats/lons.`
      },
      { cmd: 'swp-data assemble iri',
        title: 'IRI baseline',
        desc: 'Climatology baseline field. Slowest stage.',
        note: `For every frame timestamp we evaluate the IRI (International Reference Ionosphere) empirical model on the
        same 23×45 grid and integrate electron density over 97 altitude shells from 80-2000 km to get a vertical TEC.
        This gives us a physics-based "expected" ionosphere for each moment rather than asking it to predict absolute TEC from scratch. 
        Frames are grouped by day so we can hand IRI one date plus a vector of UT values at a time, which is the only thing that makes this
        tractable. Each day also pulls its F10.7 solar-flux input from the parsed daily table, falling back to the
        previous day if one is missing.`
      },
      { cmd: 'swp-data assemble dtec',
        title: 'Residual dTEC',
        desc: 'residual = tec − iri.',
        note: `Here we subtract the IRI baseline from the observed IONEX field, cell by cell, to produce the residual
        field (dTEC = observed − climatology) that is actually what the model predicts. Learning the residual rather
        than the raw TEC lets the model focus on the harder, driver-dependent departures from climatology instead of
        re-learning the smooth diurnal/seasonal pattern IRI already captures. The stage hard-fails if the observed and
        baseline timestamp vectors don't line up exactly, so a residual can never be formed from two mismatched
        instants. We deliberately keep the plasmaspheric offset that lives in IONEX-minus-IRI (it's mostly zonal, so
        the spherical model can represent it, and a learned correction can be added later), and we stamp the exact
        residual definition into every output file so that choice always travels with the data.`
      },
      { cmd: 'swp-data assemble omni',
        title: 'Align drivers',
        desc: 'Drivers aligned to dTEC frame timestamps.',
        note: `This stage resamples the solar-wind and geomagnetic drivers onto the exact timestamps of the residual
        field, producing a six-channel driver row for every dTEC frame. The two driver families are aligned on
        purpose by different rules: the five OMNI channels (B-field magnitude, By, Bz, flow speed, proton density) are
        continuous physical quantities, so they're time-interpolated, whereas Kp is a step index that is only known
        after its 3-hour window closes, so it's forward-filled, because interpolating it would leak future information into
        the present. Fill sentinels in the OMNI data are caught by magnitude threshold rather than exact equality (to
        dodge float-comparison misses), and the stage refuses to finish if any NaN survives alignment or if any dTEC
        timestamp fails to find a driver value, so a silently mis-aligned driver column can't slip through.`
      },
    ],
  },
  {
    id: 'gold',
    label: 'Gold',
    tagline: 'Model-ready',
    stages: [
      { cmd: 'swp-data assemble windows',
        title: 'Training windows',
        desc: 'Normalized input/target windows + metadata.json.',
        note: `The final stage concatenates every year, sorts by time, drops duplicates, and cuts the record into
        9-frame windows: 6 input frames the model sees and 3 target frames it must predict. A window is only kept if
        all nine timestamps are free of NaNs, strictly increasing, and identically spaced; that last rule is what stops
        a window from silently straddling a data gap or the point where the IONEX cadence changes from 2-hourly to
        hourly. Splits are temporal, by each window's start year (train ≤ 2019, validation 2020-2022, test after),
        because with a forecasting model you must never train on the future and test on the past. Crucially, the
        normalization statistics are computed from the training split alone and then applied to all three splits, the
        standard guard against leaking validation/test information into the model. Everything is accumulated and
        written in chunks so peak memory stays bounded no matter how large the dataset grows.`
      },
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
  { title: 'Export native maps',
    desc: '71×73 hourly TEC grids per year (.npz).',
    note: `For the globe we deliberately export the maps at IONEX's native 71×73 resolution rather than reusing the
    23×45 grid the model trains on. The coarse Gauss-Legendre grid is right for the network but too blocky to look
    good on screen. This exporter lives entirely outside the training pipeline and writes to its own output tree, so
    visualization work can never accidentally change anything the model consumes. It does reuse the pipeline's exact
    IONEX parser, though, so the maps you see are decoded identically to the ones the model was trained on.`
  },

  { title: 'Build day blobs',
    desc: '24 hourly maps → little-endian float16, gzipped, one file per UT day.',
    note: `Each UT day becomes a single gzipped binary blob of 24 hourly maps stored as little-endian float16. We
    chose float16 because it halves the bytes versus float32 and can be handed straight to the GPU as a half-float
    texture with no lossy conversion, and gzip then squeezes the smooth TEC field another 2-4× on top. One file per day
    keeps the request pattern simple and cache-friendly, and every blob is padded to the same size (missing hours are
    filled with NaN) so the client can index any hour by a fixed offset.`
  },

  { title: 'Upload to Cloudflare R2',
    desc: 'actual/ at the bucket root, predicted/ under a key prefix.',
    note: `The day blobs are uploaded to Cloudflare R2, an S3-compatible object store, because historical maps are
    just static files, and object storage plus a CDN needs no running server to scale, patch, or babysit. The upload sets
    per-object headers by hand (Content-Encoding: gzip on the blobs, application/json on the metadata) so the browser
    inflates each blob transparently on fetch; a blanket sync can't do this without wrongly tagging the metadata as
    gzip too. Observed maps sit at the bucket root and predicted maps live under their own key prefix, so the same
    serving path handles both layers without any redesign.`
  },

  { title: 'Worker serves /tec/*',
    desc: 'Same-origin, gzip content-encoding, immutable edge cache.',
    note: `A small Cloudflare Worker streams any /tec/* request straight out of the R2 bucket, so the browser fetches
    map data from its own origin and CORS never enters the picture. Everything else falls through to the static React
    app. Because each day's file is immutable once published, the Worker marks it cacheable for a year, so after the
    first hit the map is served from the edge cache rather than R2, which is fast for the user and cheap for us.`
  },
  { title: 'Globe renders',
    desc: 'float16 → HalfFloat DataTexture; shown hour tracks the sun as the globe spins.',
    note: `On the client the inflated float16 bytes are uploaded directly as a Three.js half-float DataTexture, with no
    decode step since the GPU consumes half-floats natively, and a single reusable texture is refilled with the
    active hour's slice instead of reallocating every frame. Rather than pinning a fixed UT hour, we pick each frame
    the UT hour whose subsolar point currently faces the sun, so the TEC crest stays over the dayside as the globe
    spins and one full rotation naturally sweeps all 24 hours. The same half-float path also backs the
    predicted-minus-actual difference overlay, keeping every render mode on one pipeline.`
  },
];

// --- Challenges & mistakes: what went wrong, and what it changed ---
// `fixLabel` overrides the second eyebrow for an item that isn't closed yet.
type Challenge = {
  title: string;
  problem: React.ReactNode;
  fix: React.ReactNode;
  fixLabel?: string;
};

const CHALLENGES: Challenge[] = [
  {
    title: 'Timestamps that were silently off by the local UTC offset',
    problem: `Frame epochs are UTC seconds by contract, but the pipeline minted them with
    datetime.timestamp() on naive datetimes, which reinterprets the value in whatever timezone the
    machine happens to be in. Every frame shifted by the local offset, and by a different amount
    either side of a DST boundary. Nothing broke: the maps still lined up with each other, so every
    downstream equality check kept passing and the only symptom was a dataset quietly misaligned
    with the drivers that are supposed to explain it.`,
    fix: `Epoch seconds are now minted in exactly one place, and _to_epoch_utc raises on a naive
    datetime rather than guessing a zone. Because code version alone cannot express this, every run
    manifest also records TZ, tzname and the UTC offset the build ran under. The regression is
    pinned by tests that assert frame epochs are byte-identical across timezones (a UTC cloud build
    must equal a laptop build) and stay evenly spaced across a DST transition.`,
  },
  {
    title: 'We pulled F10.7 from the wrong product',
    problem: `The first version of the driver path took its indices from the OMNI2 hourly product
    and CelesTrak. Both look right, and both are wrong for this pipeline: OMNI2's F10.7 is adjusted
    to 1 AU, so it cannot feed an IRI baseline that expects the observed flux, and its June 2006 Kp
    contradicts the definitive GFZ record. An F10.7 error does not stay local either: it goes into
    the IRI baseline, and the baseline goes into both the model inputs and the targets.`,
    fix: `The GFZ combined index file is now the single origin for both Kp and observed F10.7, and
    the column mapping is proven rather than trusted: the parser checks the orbital signature
    (observed/adjusted tracks (1 AU / r)², so the ratio must exceed 1.015 at January perihelion and
    fall below 0.985 in July) and aborts the build if it disappears. The source swap itself was
    gated by verify-gates, which rebuilt both index series and required them to be equal over the
    full overlap before anything downstream was allowed to move.`,
  },
  {
    title: 'An unbounded fill invented driver history',
    problem: `Aligning the solar-wind channels with a plain time interpolation draws a straight line
    across any gap, however long, and OMNI has genuine multi-day plasma outages. The fabricated
    history was worse than the gap: because interpolation leaves no NaN behind, the "raise if any
    NaN survives" guard could essentially never fire, and the window builder's finiteness test had
    nothing left to catch. Storm-time windows were being trained on driver values no instrument ever
    measured.`,
    fix: `Both fills are now bounded, and values beyond the tolerance are deliberately left NaN
    instead of filled: 120 minutes for the OMNI channels, measured per channel (an IMF outage does
    not imply a plasma outage), and one 3-hour window for the forward-filled Kp. Those NaNs are the
    point: they make the finiteness test meaningful again, so a window spanning a real outage gets
    dropped rather than silently invented. Each year also ships a per-value imputed mask and an
    imputation-rule string, so provenance travels with the data.`,
  },
  {
    title: 'A quality check that cried wolf',
    problem: `IONEX repeats the same instant at day boundaries (the 24:00 map of one day is the
    00:00 map of the next), and because the two copies come from different daily CODE solutions they
    never agree exactly. We judged that disagreement with a fixed 0.1 TECU tolerance. TEC swings
    roughly 10× over a solar cycle, so an absolute threshold fires hardest at solar maximum,
    precisely where relative agreement is best. It warned on every year from 2002 onward, which is
    the kind of noise that trains you to ignore the log entirely.`,
    fix: `Measured across the record, the disagreement is a near-constant fraction of signal: ~1.42%
    in 2002, 1.45% in 2004, 1.0% in a quiet 2019, while the raw figure ranges from 0.08 to 0.48
    TECU. The tolerance is now relative, at 5%, which leaves over 3× headroom above the observed
    baseline while a genuinely misaligned map still differs by tens of percent. The percentage is
    logged every year whether or not it warns, so the trend stays visible without the false alarms.`,
  },
  {
    title: 'We derived the spectral bandwidth on the wrong field',
    problem: `The first L_max derivation ran on raw IONEX TEC, approximating the IRI subtraction by
    removing the area-weighted spatial mean. It produced a confident answer from a hard cliff in the
    power spectrum, but that cliff is an artifact of the product, not a property of the ionosphere:
    CODE builds IONEX by fitting spherical harmonics internally up to l=15. We had measured the
    bandwidth of the file format. The elevated even degrees in the same spectrum were aliasing from
    interpolating the 5° longitude sampling, which is also not signal.`,
    fix: `The derivation was redone on the actual modelling target, the ΔTEC residual, where the
    decay is gradual and no format cliff exists. With no cliff to lean on, the Parseval energy bound
    does the real work: L_max is the smallest truncation retaining 99.9% of the residual's energy,
    which is what fixes the Gauss-Legendre 23×45 grid (N_lat = L_max + 1, N_lon = 2·L_max + 1) the
    whole pipeline is built around. Both notebooks are kept side by side, because the discarded one
    is the evidence for why the second is trustworthy.`,
  },
  {
    title: 'The archive fought back',
    problem: `Two years of training data turned out not to exist. IONEX is missing a single isolated
    day in 2020, 35 contiguous days at the end of 2022, and 218 contiguous days of 2023. These are gaps at
    the analysis center, not download failures, and no amount of retrying produces them. IGS also
    renamed the files mid-record, at DOY 219 of 2023, so an extractor written against either naming
    scheme alone fails on half the archive while looking like a network problem.`,
    fix: `The extractor tries both the legacy and long-name schemes for every day and lets the first
    success become that day's manifest row, and a 404 is recorded as a permanent skip, since that is a real data
    gap, not a transient error to retry forever. The gaps then had to be made harmless downstream,
    which is where the window rule comes from: a window is kept only if its nine timestamps are
    finite, strictly increasing, and identically spaced. That last clause is what stops a window
    from straddling a gap, or the point where the IONEX cadence changes from 2-hourly to hourly.`,
  },
  {
    title: 'Kp still leaks a little of the future',
    problem: `Kp is a step index published only after its 3-hour window closes, so it is
    forward-filled rather than interpolated, because interpolating it would obviously leak. The subtler
    problem is the stamp: windows are stamped at their start, so a frame at 01:00 receives the Kp of
    the 00:00–03:00 window, which is not published until 03:00 and encodes activity through 03:00.
    That is up to 3 hours of lookahead on a 3–9 hour forecast.`,
    fix: `Stamping at window end would close it, and we have left it open on purpose for now:
    changing it alters the driver contract and invalidates every built artifact. It is written down
    in the pipeline docs instead of quietly fixed, so any score this model posts is read with it in
    view. An honest caveat beats a silent one.`,
    fixLabel: 'Where it stands',
  },
  {
    title: 'The globe looked right on exactly one screen',
    problem: `Two rendering bugs had the same root cause: something fixed that should have been
    derived. A hard-coded camera distance framed the globe correctly on the developer's monitor and
    cropped it on tall or narrow ones, and pinning the overlay to a fixed UT hour let the TEC crest
    drift off the dayside as the Earth spun, so the brightest part of the map sat in the dark.`,
    fix: `The camera distance is now solved from the geometry: a sphere of radius r at distance d
    subtends asin(r/d), so d = r / sin(half-FOV) against whichever dimension is actually limiting at
    the current aspect ratio, which means the globe is never cropped. The displayed hour is picked
    per frame as the UT hour whose subsolar meridian faces the sun, derived from the sun's direction
    in the Earth's own frame rather than from the spin angle, so it stays correct under the 23.44°
    axial tilt, and one revolution naturally sweeps all 24 hours.`,
  },
];

// One unified flow node: the pipeline stage (what it does) and the choices /
// reasoning (why) combined in a single card, threaded onto the flow spine.
const StageNode: React.FC<{ stage: Stage; accent?: LayerId }> = ({ stage, accent }) => (
  <div className={`hw-node${accent ? ` hw-node--${accent}` : ''}`}>
    <span className="hw-node__marker" aria-hidden="true" />
    <div className="hw-node__card">
      <div className="hw-node__head">
        <h4 className="hw-node__title">{stage.title}</h4>
        {stage.cmd && <code className="hw-node__cmd">{stage.cmd}</code>}
      </div>

      <div className="hw-node__what">
        <span className="hw-node__eyebrow">What it does</span>
        <p className="hw-node__desc">{stage.desc}</p>
      </div>

      {stage.note && (
        <div className="hw-node__why">
          <span className="hw-node__eyebrow hw-node__eyebrow--why">Why / choices</span>
          <div className="hw-node__note">{stage.note}</div>
        </div>
      )}
    </div>
  </div>
);

// A challenge node: the same card as a pipeline stage, but the two halves are
// "what went wrong" and what we changed because of it.
const ChallengeNode: React.FC<{ item: Challenge }> = ({ item }) => (
  <div className="hw-node">
    <span className="hw-node__marker" aria-hidden="true" />
    <div className="hw-node__card">
      <div className="hw-node__head">
        <h4 className="hw-node__title">{item.title}</h4>
      </div>

      <div className="hw-node__what">
        <span className="hw-node__eyebrow">What went wrong</span>
        <div className="hw-node__desc">{item.problem}</div>
      </div>

      <div className="hw-node__why">
        <span className="hw-node__eyebrow hw-node__eyebrow--why">
          {item.fixLabel ?? 'What we changed'}
        </span>
        <div className="hw-node__note">{item.fix}</div>
      </div>
    </div>
  </div>
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

      {/* A single unified flow: each section is a spine-threaded track of nodes. */}
      <div className="hw-flow">
        {/* 01 Ingestion */}
        <SectionHead num="01" title="Ingestion" sub="Three raw space-weather sources." />
        <div className="hw-track">
          {SOURCES.map((s) => <StageNode key={s.title} stage={s} />)}
        </div>

        {/* 02 Processing (medallion) */}
        <SectionHead num="02" title="Processing" sub="Three medallion layers, with each stage being a CLI subcommand." />
        <div className="hw-track">
          {LAYERS.map((layer) => (
            <React.Fragment key={layer.id}>
              <div className={`hw-layerhead hw-layer--${layer.id}`}>
                <span className="hw-layerhead__dot" />
                <span className="hw-layerhead__label">{layer.label}</span>
                <span className="hw-layerhead__tagline">{layer.tagline}</span>
              </div>
              {layer.stages.map((s) => <StageNode key={s.title} stage={s} accent={layer.id} />)}
            </React.Fragment>
          ))}
        </div>

        {/* 03 Model */}
        <SectionHead num="03" title="Model" sub="Learns the residual field on the gold windows." />
        <div className="hw-track">
          <StageNode stage={MODEL} />
        </div>

        {/* 04 Serving & visualization */}
        <SectionHead num="04" title="Serving & Visualization" sub="From numpy arrays to maps on the globe." />
        <div className="hw-track">
          {SERVING.map((s) => <StageNode key={s.title} stage={s} />)}
        </div>

        {/* 05 Challenges & mistakes */}
        <SectionHead num="05" title="Challenges & Mistakes" sub="What went wrong and what we learned along the way." />
        <div className="hw-track">
          {CHALLENGES.map((c) => <ChallengeNode key={c.title} item={c} />)}
        </div>
      </div>
    </main>
  );
};
