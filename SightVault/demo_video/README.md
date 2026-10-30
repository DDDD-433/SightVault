# Submission video source

Real SightVault browser capture, deterministic chapter graphics, and local Kokoro neural narration.

Run from the SightVault project root with the app running:

1. `.video-venv/bin/python demo_video/generate_voice.py`
2. `.venv/bin/python demo_video/artwork.py`
3. `.venv/bin/python demo_video/record_demo.py`
4. `.venv/bin/python demo_video/render_video.py`
5. `.venv/bin/python demo_video/verify_video.py`

The separate video environment contains MLX-Audio, Misaki English, its language model, and local ASR QA dependencies. It does not modify the app's dependency lock. The pinned Kokoro weights live in the user's Hugging Face cache, outside the source export. The editing scripts use ffmpeg/ffprobe from the host. App interactions are native Playwright actions; recording-only CSP bypass supports cursor/trim overlays and does not change the server policy or model outputs.

Raw videos, intermediate waveforms, per-scene clips and image renders are working files. Finished deliverables are under `exports/submission/`. Keep the raw recording local; it contains editing markers. Captions use approximate phrase timing. Automated audio transcription is a sanity check; human preference should decide between the AI and teammate voice versions.
