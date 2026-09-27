# Design

Plumbline is a tool an organizer runs for a dozen events a year and a judge
uses for hours at a time. The design has three jobs: look like nothing else in
its category, stay calm enough to work in all day, and hold up to real use.

The whole system is visible at `/design/`.

## Principles

1. **Decisions, not defaults.** No gradients, no glass, no glow, no drop
   shadows as decoration, no rounded shadowed cards, no emoji, no icons in
   circles, no stock page skeleton. Every number on screen comes from data.
2. **Quiet everywhere, loud once.** One signature per screen and nothing
   competing with it. Data screens have no animation and no ornament.
3. **Usable for real.** Contrast is computed, not eyeballed. Everything works
   from the keyboard. Every list has an empty state and every form says how to
   fix what went wrong.

## The plumb line

The name is the design. A plumb line shows how far something is out of true,
and that is what judging normalization does. So deviation from a vertical
reference is the one visual idea, used four ways:

| screen | signature | what it shows |
|---|---|---|
| Landing | one plumb line per judge, swinging and settling | each judge's lean from the panel mean |
| Results | slopegraph, raw order to final order | which projects moved and how far |
| Event | the dates on one rule, with a bob at today | where the event is in its life |
| Organizer calibration | elevation against a datum line | each judge's mean and spread |
| Judge review | graduated rod | the score scale as a measuring instrument |

## The public pages

**Landing.** The lines hang in the upper part of the first screen and the
headline stands under them; no line crosses a letter. Every line is a judge
of the most judged published event on the installation, drawn without name
or id: lean is the judge's mean minus the panel's, length is the number of
reviews, dashed is a judge who gave every project the same mark. The counter
reads the mean lean still on show, in rubric points, and reaches 0.00 when
the field has settled. The loop then stops, and it stops whenever the field
leaves the screen.

The headline is chosen from the data (`judging/showcase.py`). If the panel
has a judge who gave every project the same mark, the page opens with that
and with the project the mark was holding up. Otherwise it opens with the
project that moved furthest. If nothing is published, it says so. No number
on the page is written by hand; `tests/test_showcase.py` checks each one
against the database.

**Results.** Scores are shown the way Hackathon Raptors publishes its own:
`3.930 adjusted / 5.00 · 3 judges`, with the mean of every criterion. The
slopegraph is drawn on the server as SVG, so it is there with scripts off;
the script only follows one project across on hover or focus. Under 1100px
the graph gives way to the table, which on a phone keeps place, project,
judges, adjusted score and movement.

**Event.** The dates are one rule. Under 800px the rule gives way to a table
of the same dates.

## Tokens

All in `static/css/plumbline.css`, in the first forty lines.

- **Type.** Archivo (variable width and weight) for display and text,
  JetBrains Mono for data and labels. Both under the SIL Open Font License,
  subset to Latin, served from the image: 93 KB together.
- **Colour.** Four inputs: ground, ink, accent, signal. Everything else
  (surfaces, greys, lines, washes) is derived with `color-mix()`. One extra
  fixed colour, caution.
- **Spacing.** 4, 8, 12, 16, 24, 40, 64, 100, 160, 256. Nothing else.
- **Grid.** 12 columns, 28px column gap, 8px row gap, 32px page margin;
  4 columns under 800px.
- **Motion.** One curve, `cubic-bezier(.17, .84, .44, 1)`; 220ms for state,
  620ms for movement. Properties are always named. Reduced motion is honoured.
- **Radius.** Zero.

## Event themes

Each event sets four colours in Settings, or picks a preset. The greys are
derived per theme so that secondary text reaches 5:1 on that ground; a mid-tone
ground gets more ink in its grey than a black one. A theme is **refused at save
time**, in the form and in the API, if text, secondary text, accent, signal or
button text would fall under WCAG AA (4.5:1). See `events/theme.py`.

Presets: Plumbline dark, Drafting film, Black and amber, Cream and oxblood,
Cyanotype, Charcoal and red.

## What is enforced by tests

`tests/test_theme.py` fails the build if:

- any preset falls under the contrast thresholds;
- the stylesheet contains `transition: all`, a box shadow, a backdrop filter,
  a banned typeface or a non-token radius;
- a colour literal appears outside the token blocks;
- any template or stylesheet loads anything over the network;
- a script holds a colour literal or makes a request (`tests/test_showcase.py`).
