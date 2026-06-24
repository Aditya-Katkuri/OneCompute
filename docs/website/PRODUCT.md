# Product

## Register

brand

> Context note: this is the **brand-register** context for the OneCompute **marketing website**
> (`src/website/index.html`). It is intentionally separate from the project's root
> `docs/PRODUCT.md` / `docs/DESIGN.md`, which describe the **product-register dashboard**
> (the Fleet Console). impeccable's auto-loader resolves the root files for any target, so when
> running an impeccable command on the website, work in the **brand** register (the task cue
> "marketing website / landing" and the surface in focus `src/website/` both select it) and read
> these two files as the website's context.

## Users

The public **marketing / pitch surface** for OneCompute (the NightShift idle-compute grid).
One audience, one job:
- **A skeptical, time-poor visitor** — a Microsoft stakeholder, hackathon judge, prospective
  internal customer, or engineer — landing cold, deciding in seconds whether "harvest the idle
  compute you already own" is real and worth a click into the console.

Context of use: a desktop browser, often shown live or linked from a deck. The visitor scrolls
once, top to bottom. The page must earn trust and curiosity faster than they can bounce.

## Product Purpose

Make the idea *land viscerally before it's explained*: every company already owns a second
supercomputer scattered across idle laptops, and OneCompute harvests that spare headroom into a
private, opt-in grid that steps aside the instant someone needs their machine back. The page opens
on an interactive WebGL dawn lake (idle compute at rest), turns spilled water into energy droplets
that a terminal harvests (workloads routed, compute reclaimed), then grounds the metaphor in honest
numbers — the cost of the build-everything reflex, what idle harvest reclaims, and the measured
throughput shown beside (never instead of) the disclosed theoretical ceiling. Success = a visitor
who arrives skeptical and leaves wanting to open the console.

## Brand Personality

Premium, dreamlike, and quietly confident — the calm of a lake at dawn, not the noise of a SaaS
launch. Editorial restraint over hype. The voice is plain and exact ("Idle machines, woken at
dawn." / "The honest number."), and the motion is the argument: the product's behavior (compute at
rest, work routed, energy reclaimed) is *shown* in the interaction, not asserted in copy.

## Anti-references

- Generic AI-default landing pages: cream-and-serif editorial clichés, near-black with one acid
  accent, the big-gradient-number hero template, an eyebrow chip above every section.
- Loud, busy SaaS marketing: autoplaying hype, decorative motion that means nothing, stock 3D blobs.
- Vanity throughput: any headline performance figure shown without the honest measured number and
  the disclosed 1.8-ExaOPS theoretical ceiling beside it.
- Templated component grids: identical icon-heading-text cards repeated down the page.

## Design Principles

1. **The motion is the message.** Every signature interaction encodes a true fact about the product
   — water at rest = idle compute, ripples/poofs = the system responding to touch, droplets → terminal
   = workloads routed and energy reclaimed. Motion that doesn't carry meaning is cut.
2. **Dreamlike, never busy.** Soft drifting pastels, slow ambient life, one memorable signature per
   scene. Spend boldness on the lake; keep everything around it quiet.
3. **Honest by construction.** Measured throughput always sits beside the theoretical ceiling, each
   labeled for what it is. The pitch is credible because it discloses its own limits.
4. **Earn the scroll.** The hero states the whole idea in one line and one interaction; each section
   below pays off a real question (the cost, the mechanism, the trust model) rather than scaffolding.
5. **Self-contained and accessible.** Ships as a single file with zero external requests; full
   keyboard access, visible focus, and a complete `prefers-reduced-motion` path are non-negotiable.

## Accessibility & Inclusion

WCAG 2.2 AA: body text ≥4.5:1 / large ≥3:1 (no gradient text — the one place contrast fails);
visible `:focus-visible` rings; a skip-to-content link; targets ≥24px (≥44 for primary). Every
animation has a reduced-motion alternative (the WebGL field renders a static frame; GSAP reveals
default visible), and content never depends on motion to be legible. Self-contained fonts so type
is identical offline.
