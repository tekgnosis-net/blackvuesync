# BlackVue recording file formats

A durable reference for the file-naming convention and on-disk content of BlackVue
dashcam recordings synchronized by this tool. The binary/text formats below were
**verified against a real DR-series sample** (June 2026); older community tools
(`bartbroere/blackvue-acc`, `gandy92/blackclue`) decoded the same telemetry when it
was embedded inside the MP4, whereas current firmware writes separate sidecar files
-- the inner formats align.

## Filename convention

```text
YYYYMMDD_HHMMSS_<type><direction>[upload].<ext>
```

| Part | Meaning |
| --- | --- |
| `YYYYMMDD_HHMMSS` | local timestamp (camera clock / timezone) |
| `<type>` | one recording-type letter (table below) |
| `<direction>` | camera direction letter (`F`/`R`); video + thumbnail carry it, the `.3gf`/`.gps` omit it |
| `[upload]` | optional `L` (live) / `S` (substream) flag |
| `<ext>` | `mp4` \| `thm` \| `3gf` \| `gps` |

The filename regex lives in `blackvuesync/sync.py` (`filename_re`) and is parsed into a
`Recording` dataclass by `to_recording()`.

### Per-recording-instant file set

All artifacts of one recording instant share the base `YYYYMMDD_HHMMSS_<type>`:

| File | Content | Per |
| --- | --- | --- |
| `<base><dir>.mp4` | H.264 video | camera direction (e.g. `_PF.mp4` front, `_PR.mp4` rear) |
| `<base><dir>.thm` | JPEG thumbnail | camera direction |
| `<base>.3gf` | G-sensor / accelerometer (binary) | recording (no direction) |
| `<base>.gps` | GPS track (NMEA text) | recording (no direction) |

So front and rear videos pair by sharing base+type and differing only by the
direction letter; they share a single `.gps` and `.3gf`.

## Recording-type codes

| Code | Meaning |
| --- | --- |
| `N` | Normal (continuous while driving) |
| `E` | Event (impact, sudden braking, or swerving) |
| `P` | Parking (motion or impact while parked) |
| `M` | Manual (button press / proximity-sensor tap) |
| `R` | Manual backup (clips saved while reviewing) |
| `T` | Timelapse |

`blackvuesync/sync.py` recognizes a broader set across models
(`NEPMIOATBRXGDLYF`: e.g. `I` impact, `O` overspeed, `A` acceleration, `B` braking,
`R`/`X`/`G` geofence, `D`/`L`/`Y`/`F` DMS). The viewer displays the letter plus a
best-effort label.

> Note the collision: `R` is both a recording **type** (manual backup) and a camera
> **direction** (rear). Position in the filename disambiguates -- the type letter
> immediately follows the timestamp; the direction letter (if any) follows the type.

## Camera-direction codes

| Code | Meaning |
| --- | --- |
| `F` | front camera |
| `R` | rear camera (or interior on some 3-channel models) |
| `I` / `O` | interior / optional (recognized by `sync.py`; uncommon) |

## `.gps` -- GPS track (NMEA-0183 text)

- Plain text. Each NMEA sentence is **prefixed by a wall-clock timestamp in
  `[milliseconds-since-epoch]`** and terminated by CRLF; blank lines separate entries.
- The talker is **multi-GNSS `$GN...`** (GPS+GLONASS+Galileo), **not** `$GP...`.
  Parse **by sentence type, talker-agnostic** (`$G?RMC` / `$G?GGA`).
- Sentences observed: `$G?RMC` (position, speed-over-ground in **knots**, date) and
  `$G?GGA` (position, fix quality, satellite count, altitude).
- Coordinates are `DDMM.mmmmm` + hemisphere (`N`/`S`/`E`/`W`) -> decimal degrees:
  `degrees + minutes/60`, negated for `S`/`W`.
- Example (anonymized real framing):

  ```text
  [1780855916491]$GNRMC,HHMMSS.00,A,DDMM.mmmmm,S,DDDMM.mmmmm,E,0.000,,DDMMYY,,,A,V*06
  [1780855916491]$GNGGA,HHMMSS.00,DDMM.mmmmm,S,DDDMM.mmmmm,E,1,12,0.68,52.8,M,19.4,M,,*6B
  ```

- A stationary/parked recording may contain a single fix (speed `0.000`); some
  recordings have no `.gps` file, or a `.gps` with no valid fix.

## `.3gf` -- G-sensor / accelerometer (binary)

- **Big-endian, fixed 10-byte records, packed back-to-back, no header.** File size is
  always a multiple of 10 (`size / 10` = sample count).
- Record layout (`struct` format `>Ihhh`):

  | Offset | Type | Field |
  | --- | --- | --- |
  | 0 | `uint32` (BE) | milliseconds from recording start |
  | 4 | `int16` (BE) | X axis (raw) |
  | 6 | `int16` (BE) | Y axis (raw) |
  | 8 | `int16` (BE) | Z axis (raw) |

- Sample rate **≈ 10 Hz** (observed ~105 ms between samples; a ~60 s recording yields
  ~560 samples).
- Axis values are raw `int16`. A stationary recording reads a near-constant vector
  whose magnitude ≈ 1 g (gravity), implying a scale of **≈ raw / 128 -> g**. Confirm
  the canonical divisor against `bartbroere/blackvue-acc` before relying on absolute
  g-values; relative magnitude is reliable regardless.

## `.thm` -- thumbnail

Baseline JPEG (JFIF), 704×480 in the sample. Served to the browser as `image/jpeg`
(the `.thm` extension is not in the standard MIME map).

## `.mp4` -- video

H.264 in an MP4 (`ftyp mp42`) container -- browser-native, **no transcoding needed**.
Front and rear are separate files sharing base+type, differing by direction.

## Time alignment (for the viewer)

`.gps` timestamps are **absolute epoch-ms**; `.3gf` timestamps are **ms-from-start**.
Both reduce to "elapsed seconds from recording start", which maps directly to HTML5
`video.currentTime`. Across auto-advanced consecutive segments, the absolute GPS
epoch provides a continuous wall-clock timeline so the map path and telemetry stay
coherent rather than resetting per segment.

## References

- [`bartbroere/blackvue-acc`](https://github.com/bartbroere/blackvue-acc) -- `.3gf`
  accelerometer extraction.
- [`gandy92/blackclue`](https://github.com/gandy92/blackclue) -- GPS and acceleration
  extraction from BlackVue MP4s.
