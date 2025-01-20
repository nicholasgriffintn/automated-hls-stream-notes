import base64
import json
import logging
from typing import Optional
from datetime import datetime
import os

import config
from utils import clean_transcript
from cloudflare import CloudflareService

class TranscriptionService(CloudflareService):
    def __init__(self, logger: logging.Logger, transcript_json_path: Optional[str] = None):
        super().__init__(logger)
        self.transcript_json_path = transcript_json_path
        
        if transcript_json_path:
            with open(transcript_json_path, "w") as f:
                f.write("[\n")
    
    async def transcribe_audio(self, audio_chunk: bytes) -> Optional[tuple[str, datetime]]:
        """Transcribe an audio chunk using Cloudflare's API"""
        wav_header = bytes([
            0x52, 0x49, 0x46, 0x46,  # "RIFF"
            0x24, 0x00, 0x00, 0x00,  # Chunk size
            0x57, 0x41, 0x56, 0x45,  # "WAVE"
            0x66, 0x6D, 0x74, 0x20,  # "fmt "
            0x10, 0x00, 0x00, 0x00,  # Subchunk1 size
            0x01, 0x00,              # AudioFormat (PCM)
            0x01, 0x00,              # NumChannels (1)
            0x80, 0x3E, 0x00, 0x00,  # SampleRate (16000)
            0x00, 0x7D, 0x00, 0x00,  # ByteRate
            0x02, 0x00,              # BlockAlign
            0x10, 0x00,              # BitsPerSample (16)
            0x64, 0x61, 0x74, 0x61,  # "data"
            0x00, 0x00, 0x00, 0x00   # Subchunk2 size
        ])
        
        audio_data = wav_header + audio_chunk
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        self.logger.debug("Sending audio chunk for transcription...")
        request = {
            "type": "universal.create",
            "request": {
                "eventId": f"transcribe_{datetime.now().timestamp()}",
                "provider": "workers-ai",
                "endpoint": "@cf/openai/whisper-large-v3-turbo",
                "headers": {
                    "Authorization": f"Bearer {config.CF_TOKEN}",
                    "Content-Type": "application/json",
                },
                "query": {
                    "audio": audio_base64,
                    "task": "transcribe",
                    "language": "en",
                    "vad_filter": "true"
                }
            }
        }
        
        response = await self._send_request(request)
        if not response:
            return None
        
        self.logger.debug(f"Raw response: {json.dumps(response, indent=2)}")
        
        if response.get("type") == "universal.created":
            response_data = response.get("response", {})
            self.logger.debug(f"Response data: {json.dumps(response_data, indent=2)}")
            
            result = response_data.get("result", {})
            self.logger.debug(f"Result: {json.dumps(result, indent=2)}")
            
            if isinstance(result, dict):
                text = result.get("text", "")
                if text:
                    timestamp = datetime.now()
                    
                    if self.transcript_json_path:
                        try:
                            file_size = os.path.getsize(self.transcript_json_path)
                            
                            with open(self.transcript_json_path, "a") as f:
                                if file_size > 3:
                                    f.write(',\n')
                                json.dump(response_data, f, indent=2)
                                f.flush()
                        except Exception as e:
                            self.logger.error(f"Failed to write transcription data to JSON: {e}")
                    
                    self.logger.info(f"Successfully transcribed: {text[:100]}...")
                    return clean_transcript(text), timestamp
                else:
                    self.logger.warning("Transcription returned empty text")
            else:
                self.logger.error(f"Unexpected result format: {result}")
        else:
            self.logger.error(f"Unexpected response type: {response.get('type')}")
        
        return None
        
    def close(self):
        """Close the JSON file by writing the closing bracket"""
        if self.transcript_json_path:
            with open(self.transcript_json_path, "a") as f:
                f.write('\n]')
                f.flush()