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
uv run main.py
```

You should see the output in the `outputs` directory as it comes in.
