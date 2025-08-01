> Update: I've made this into an app on my Polychat application, it will transcribe live from a tab so does basically what this does but in a better, unified way. I've archived this as I don't think i'll be working on it.
> You can find my ai platform here: https://github.com/nicholasgriffintn/ai-platform

# Automated HLS Stream Notes

An automated system to generate notes from an HLS video streams.

To get started, you need to start a podman machine:

```bash
podman machine init && podman machine start
```

Then build the container with the following command:

```bash
podman build -t automated-hls-stream-notes .
```

Then run the container with the following command:

```bash
podman run --env-file .env --name automated-hls-stream-notes -v $(pwd):/app -d automated-hls-stream-notes
```

Or you can run without Podman:

```bash
uv run src/main.py <stream_url> [output_dir] [summary_interval_minutes]
```

You should see the output in the `outputs` directory as it comes in.
