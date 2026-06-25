# OneCompute resource kit

Free, drop-in resources for cinematic/video-like pages, vendored here so they work offline with no keys. Live demo: `http://localhost:8777/kit/`.

## What is in here

### lib/ (JavaScript, self-hosted)
| File | What | Use |
|---|---|---|
| `../vendor/gsap/*` | GSAP 3.15 + 15 free plugins | timeline, SplitText, MorphSVG, Flip, etc (already vendored one level up) |
| `model-viewer.min.js` | Google model-viewer (Apache-2.0) | `<model-viewer src="x.glb" camera-controls auto-rotate>` 3D in one tag |
| `three.r134.min.js` + `vanta.*.min.js` | Vanta animated backgrounds (MIT) | `VANTA.FOG({el:'#bg', ...})` one-call moving backdrop |
| `Tone.js` | Tone.js (MIT) | synth risers/booms/drones synced to the GSAP timeline, zero audio files |
| `howler.min.js` | Howler (MIT) | play SFX/music, sprites, fades |
| `lottie.min.js` | lottie-web (MIT) | play any Lottie/.json icon animation |
| `tsparticles.confetti.bundle.min.js` | tsParticles confetti (MIT) | `confetti({...})` celebratory bursts |
| `granim.min.js` | Granim (MIT) | animated gradient canvas |
| `cobe.esm.js` | Cobe (MIT) | tiny WebGL globe |
| `sample.glb` | Khronos sample model (CC-BY) | demo asset for model-viewer |

### icons3d/ (Microsoft Fluent Emoji 3D, MIT)
14 glossy 3D PNG icons (rocket, sparkles, brain, high voltage, desktop, robot, gear, battery, locked, light bulb, chart, money bag, globe, crescent moon). Drop in with `<img>` + animate with GSAP. Grab more from https://github.com/microsoft/fluentui-emoji (assets/<Name>/3D/<name>_3d.png).

### sfx/ (synthesized starter sounds, license-free)
`click, hover, whoosh, riser, impact, success` (.wav), generated with ffmpeg. Functional placeholders. For designed sound, drop in **Kenney** (CC0, kenney.nl) or **Mixkit** (attribution-free, mixkit.co) packs and load via Howler.

## Quick patterns
```html
<!-- 3D object --><model-viewer src="lib/sample.glb" camera-controls auto-rotate></model-viewer>
<!-- background --><div id="bg"></div><script>VANTA.FOG({el:'#bg',highlightColor:0x2f81f7})</script>
<!-- sound --><script>new Howl({src:['sfx/whoosh.wav']}).play()</script>
<!-- riser --><script>await Tone.start();/* synth ramp */</script>
<!-- confetti --><script>confetti({particleCount:140,spread:80})</script>
```

## Not vendored (need your account/key) — see the prioritized add-list in `Hackathon/animation-toolkit.md`.
Voiceover (ElevenLabs / Azure Speech), music (Suno / Pixabay), AI image (fal.ai / Replicate), AI video B-roll (Veo / Runway), Spline 3D scenes, Lordicon animated icons, Unicorn Studio backgrounds.
