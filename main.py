import ffmpeg
import asyncio
import websockets
import json
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
import logging
import base64
import os
from dotenv import load_dotenv

@dataclass
class Note:
    timestamp: datetime
    content: str
    category: str
    key_points: List[str]
    entities: List[str]
    importance: int

@dataclass
class IntervalSummary:
    start_time: datetime
    end_time: datetime
    key_points: List[str]
    main_topics: List[str]
    important_events: List[str]
    action_items: List[str]

class StreamProcessor:
    def __init__(self, stream_url, output_dir="outputs", summary_interval_minutes=5):
        load_dotenv()
        
        self.base_output_dir = Path(output_dir)
        self.base_output_dir.mkdir(exist_ok=True)
        
        stream_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stream_name = Path(stream_url).stem or "stream"
        self.output_dir = self.base_output_dir / f"{stream_name}_{stream_timestamp}"
        self.output_dir.mkdir(exist_ok=True)
        
        self.subdirs = {
            'logs': self.output_dir / 'logs',
            'transcripts': self.output_dir / 'transcripts',
            'summaries': self.output_dir / 'summaries',
            'reports': self.output_dir / 'reports'
        }
        for subdir in self.subdirs.values():
            subdir.mkdir(exist_ok=True)
        
        self.logger = logging.getLogger(f"StreamProcessor_{stream_timestamp}")
        self.logger.setLevel(logging.ERROR)
        
        if not self.logger.handlers:
            file_handler = logging.FileHandler(self.subdirs['logs'] / "process.log")
            file_handler.setLevel(logging.ERROR)
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
            
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_formatter = logging.Formatter('%(levelname)s: %(message)s')
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)
        
        self.logger.propagate = False
        
        self.logger.info(f"Initializing StreamProcessor with URL: {stream_url}")
        self.stream_url = stream_url
        self.summary_interval = timedelta(minutes=summary_interval_minutes)
        
        self.cf_endpoint = "wss://gateway.ai.cloudflare.com/v1"
        self.account_id = os.getenv("CF_ACCOUNT_ID")
        self.gateway_id = os.getenv("CF_GATEWAY_ID")
        self.cf_token = os.getenv("CF_TOKEN")
        
        if not all([self.account_id, self.gateway_id, self.cf_token]):
            raise ValueError(
                "Missing required environment variables. Please ensure CF_ACCOUNT_ID, "
                "CF_GATEWAY_ID, and CF_TOKEN are set in your .env file."
            )
        
        self.current_interval_notes: List[Note] = []
        self.all_interval_summaries: List[IntervalSummary] = []
        self.last_summary_time = None
        self.context_buffer = []
        self.context_window_size = 5
        
    async def process_stream(self):
        """Main processing loop"""
        process = None
        try:
            self.logger.info("Starting ffmpeg process...")
            
            process = (
                ffmpeg
                .input(self.stream_url)
                .output(
                    'pipe:',
                    f='wav',
                    acodec='pcm_s16le',
                    ac=1,
                    ar='16000',
                    loglevel='error',
                    bufsize='2M'
                )
                .overwrite_output()
                .run_async(pipe_stdout=True, pipe_stderr=True)
            )
            
            self.logger.info("ffmpeg process started successfully")
            
            # 15 seconds of audio: sample_rate * bytes_per_sample * seconds
            chunk_size = 16000 * 2 * 15
            audio_buffer = bytearray()
            min_buffer_size = chunk_size
            overlap_size = chunk_size // 4  # 25% overlap (3.75 seconds)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.last_summary_time = datetime.now()
            
            files = {
                'transcript': self.subdirs['transcripts'] / "transcript.txt",
                'transcript_json': self.subdirs['transcripts'] / "transcript.json",
                'interval_summaries': self.subdirs['summaries'] / "interval_summaries.txt",
                'current_summary': self.subdirs['summaries'] / "current_summary.txt",
                'final_report': self.subdirs['reports'] / "final_report.md"
            }
            
            with open(files['transcript_json'], 'w') as f:
                f.write('[\n')
            
            metadata = {
                'stream_url': self.stream_url,
                'start_time': timestamp,
                'summary_interval_minutes': self.summary_interval.total_seconds() / 60
            }
            with open(self.output_dir / 'metadata.json', 'w') as f:
                json.dump(metadata, f, indent=2)
            
            file_handles = {name: file.open('w', encoding='utf-8') 
                          for name, file in files.items() if name != 'transcript_json'}
            
            websocket = await self._connect_websocket()
            
            processed_transcripts = set()
            
            try:
                while True:
                    try:
                        audio_chunk = process.stdout.read(chunk_size)
                        if not audio_chunk:
                            self.logger.warning("No more audio data received")
                            break
                        
                        audio_buffer.extend(audio_chunk)
                        
                        if len(audio_buffer) >= min_buffer_size:
                            self.logger.debug(f"Processing audio buffer of size: {len(audio_buffer)}")
                            transcription = await self._transcribe_audio(bytes(audio_buffer))
                            
                            if transcription:
                                cleaned_transcription = self._clean_transcript(transcription)
                                if cleaned_transcription:
                                    self.logger.debug(f"Transcribed text: {cleaned_transcription[:100]}...")
                                    
                                    if not self.context_buffer or cleaned_transcription != self.context_buffer[-1]['text']:
                                        self.context_buffer.append({
                                            'timestamp': datetime.now(),
                                            'text': cleaned_transcription
                                        })
                                        self.context_buffer = self.context_buffer[-self.context_window_size:]
                                        
                                        file_handles['transcript'].write(
                                            f"{datetime.now()}: {cleaned_transcription}\n")
                                        file_handles['transcript'].flush()
                                
                        audio_buffer = audio_buffer[-overlap_size:]
                        
                        current_time = datetime.now()
                        
                        if current_time - self.last_summary_time >= self.summary_interval:
                            self.logger.info("Generating interval summary...")
                            
                            new_transcripts = [
                                transcript for transcript in self.context_buffer 
                                if transcript['text'] not in processed_transcripts
                            ]
                            
                            for transcript in new_transcripts:
                                note = await self._generate_enhanced_notes(transcript['text'])
                                if note:
                                    self.current_interval_notes.append(note)
                                processed_transcripts.add(transcript['text'])
                            
                            if self.current_interval_notes:
                                await self._generate_interval_summary(
                                    file_handles['interval_summaries'],
                                    file_handles['current_summary']
                                )
                            self.last_summary_time = current_time
                        
                    except KeyboardInterrupt:
                        self.logger.info("Received keyboard interrupt, shutting down gracefully...")
                        break
                    except Exception as e:
                        self.logger.error(f"Error in processing loop: {e}", exc_info=True)
                        break
                
                if self.context_buffer:
                    self.logger.info("Generating final interval summary...")
                    
                    new_transcripts = [
                        transcript for transcript in self.context_buffer 
                        if transcript['text'] not in processed_transcripts
                    ]
                    
                    for transcript in new_transcripts:
                        note = await self._generate_enhanced_notes(transcript['text'])
                        if note:
                            self.current_interval_notes.append(note)
                        processed_transcripts.add(transcript['text'])
                    
                    if self.current_interval_notes:
                        await self._generate_interval_summary(
                            file_handles['interval_summaries'],
                            file_handles['current_summary']
                        )
                
                self.logger.info("Generating final report...")
                await self._generate_final_report(file_handles['final_report'])
                    
            finally:
                with open(files['transcript_json'], 'a') as f:
                    f.write('\n]')
                
                self.logger.info("Closing file handles...")
                for fh in file_handles.values():
                    fh.close()
                await websocket.close()
                    
        except Exception as e:
            self.logger.error(f"Error processing stream: {e}", exc_info=True)
            raise
        finally:
            if process:
                try:
                    self.logger.info("Cleaning up ffmpeg process...")
                    process.kill()
                except Exception as e:
                    self.logger.warning(f"Error while killing ffmpeg process: {e}", exc_info=True)

    async def _connect_websocket(self):
        """Helper method to create websocket connection with proper headers"""
        headers = {
            "cf-aig-authorization": f"Bearer {self.cf_token}",
            "Authorization": f"Bearer {self.cf_token}"
        }
        return await websockets.connect(
            f"{self.cf_endpoint}/{self.account_id}/{self.gateway_id}",
            additional_headers=headers
        )

    async def _generate_interval_summary(self, interval_file, current_file):
        """Generate summary for the current interval"""
        if not self.current_interval_notes:
            self.logger.info("No notes to summarize for this interval")
            return

        try:
            self.logger.info("Generating interval summary...")
            async with await self._connect_websocket() as websocket:
                notes_text = "\n".join(
                    f"Time: {note.timestamp}\n"
                    f"Content: {note.content}\n"
                    f"Category: {note.category}\n"
                    f"Key Points: {', '.join(note.key_points)}\n"
                    f"Entities: {', '.join(note.entities)}\n"
                    f"Importance: {note.importance}\n"
                    for note in self.current_interval_notes
                )
                
                self.logger.debug(f"Generating summary from notes: {notes_text[:200]}...")
                
                prompt = f"""
                Analyze these notes from the last {self.summary_interval.total_seconds() // 60} minutes:

                {notes_text}

                Create a structured summary including:
                1. Key points discussed
                2. Main topics covered
                3. Important events or decisions
                4. Action items identified

                Format as JSON with structure:
                {{
                    "key_points": ["..."],
                    "main_topics": ["..."],
                    "important_events": ["..."],
                    "action_items": ["..."]
                }}
                """
                
                request = {
                    "type": "universal.create",
                    "request": {
                        "eventId": f"summary_{datetime.now().timestamp()}",
                        "provider": "workers-ai",
                        "endpoint": "@cf/meta/llama-2-7b-chat-int8",
                        "headers": {
                            "Authorization": f"Bearer {self.cf_token}",
                            "Content-Type": "application/json",
                        },
                        "query": {
                            "messages": [{"role": "user", "content": prompt}]
                        }
                    }
                }
                
                await websocket.send(json.dumps(request))
                response = json.loads(await websocket.recv())
                
                if response["type"] == "universal.created":
                    llm_response = response["response"]["result"]["response"]
                    self.logger.debug(f"Raw LLM response: {llm_response[:200]}...")
                    
                    json_str = llm_response[llm_response.find('{'):llm_response.rfind('}')+1]
                    self.logger.debug(f"Extracted JSON: {json_str[:200]}...")
                    
                    analysis = json.loads(json_str)
                    self.logger.debug(f"Parsed analysis: {json.dumps(analysis)[:200]}...")
                    
                    summary = IntervalSummary(
                        start_time=self.last_summary_time,
                        end_time=datetime.now(),
                        key_points=analysis.get('key_points', []),
                        main_topics=analysis.get('main_topics', []),
                        important_events=analysis.get('important_events', []),
                        action_items=analysis.get('action_items', [])
                    )
                    
                    interval_file.write(
                        f"\n{'='*50}\n"
                        f"Summary for period: {summary.start_time} to {summary.end_time}\n"
                        f"{'='*50}\n\n"
                    )
                    
                    interval_file.write("Key Points:\n")
                    for point in summary.key_points:
                        interval_file.write(f"- {point}\n")
                        
                    interval_file.write("\nMain Topics:\n")
                    for topic in summary.main_topics:
                        interval_file.write(f"- {topic}\n")
                        
                    interval_file.write("\nImportant Events:\n")
                    for event in summary.important_events:
                        interval_file.write(f"- {event}\n")
                        
                    interval_file.write("\nAction Items:\n")
                    for item in summary.action_items:
                        interval_file.write(f"- {item}\n")
                    
                    interval_file.write("\n")
                    interval_file.flush()
                    
                    current_file.seek(0)
                    current_file.truncate()
                    current_file.write(
                        f"Current Summary (as of {datetime.now()})\n\n"
                        f"Key Points:\n"
                    )
                    for point in summary.key_points:
                        current_file.write(f"- {point}\n")
                    current_file.flush()
                    
                    self.all_interval_summaries.append(summary)
                    
                    self.current_interval_notes = []
                    
        except Exception as e:
            self.logger.error(f"Error generating interval summary: {e}", exc_info=True)

    async def _generate_final_report(self, report_file):
        """Generate comprehensive final report"""
        try:
            self.logger.info("Generating final report...")
            async with await self._connect_websocket() as websocket:
                summaries_text = "\n".join(
                    f"Period {i+1}: {summary.start_time} to {summary.end_time}\n" +
                    f"Key Points: {', '.join(summary.key_points)}\n" +
                    f"Main Topics: {', '.join(summary.main_topics)}\n" +
                    f"Important Events: {', '.join(summary.important_events)}\n" +
                    f"Action Items: {', '.join(summary.action_items)}\n"
                    for i, summary in enumerate(self.all_interval_summaries)
                )
                
                prompt = f"""
                Create a comprehensive final report from these interval summaries:

                {summaries_text}

                The report should include:
                1. Executive Summary
                2. Key Themes and Patterns
                3. Timeline of Important Events
                4. All Action Items
                5. Conclusions and Next Steps

                Format the report in Markdown.
                """
                
                request = {
                    "type": "universal.create",
                    "request": {
                        "eventId": f"report_{datetime.now().timestamp()}",
                        "provider": "workers-ai",
                        "endpoint": "@cf/meta/llama-2-7b-chat-int8",
                        "headers": {
                            "Authorization": f"Bearer {self.cf_token}",
                            "Content-Type": "application/json",
                        },
                        "query": {
                            "messages": [{"role": "user", "content": prompt}]
                        }
                    }
                }
                
                await websocket.send(json.dumps(request))
                response = json.loads(await websocket.recv())
                
                if response["type"] == "universal.created":
                    report_file.write(response["response"]["result"]["response"])
                    report_file.flush()
                    
        except Exception as e:
            self.logger.error(f"Error generating final report: {e}", exc_info=True)

    async def _transcribe_audio(self, audio_chunk):
        """Send audio chunk to Cloudflare AI for transcription"""
        try:
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
            async with await self._connect_websocket() as websocket:
                request = {
                    "type": "universal.create",
                    "request": {
                        "eventId": f"transcribe_{datetime.now().timestamp()}",
                        "provider": "workers-ai",
                        "endpoint": "@cf/openai/whisper-large-v3-turbo",
                        "headers": {
                            "Authorization": f"Bearer {self.cf_token}",
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
                
                self.logger.debug(f"Sending request: {json.dumps(request)[:200]}...")
                await websocket.send(json.dumps(request))
                
                raw_response = await websocket.recv()
                self.logger.debug(f"Received raw response: {raw_response}")
                
                try:
                    response = json.loads(raw_response)
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse response JSON: {e}")
                    return ""
                
                self.logger.debug(f"Parsed response: {json.dumps(response)}")
                
                if response.get("type") == "error":
                    self.logger.error(f"API returned error: {response.get('error', {}).get('message', 'Unknown error')}")
                    return ""
                
                if response.get("type") == "universal.created":
                    response_data = response.get("response", {})
                    result = response_data.get("result", {})
                    
                    if isinstance(result, dict):
                        text = result.get("text", "")
                        if text:
                            timestamp = datetime.now()
                            transcription_data = {
                                "timestamp": timestamp.isoformat(),
                                "transcription_info": result.get("transcription_info", {}),
                                "segments": result.get("segments", []),
                                "text": text,
                                "word_count": result.get("word_count", 0)
                            }
                            
                            transcript_json_path = self.subdirs['transcripts'] / "transcript.json"
                            file_size = os.path.getsize(transcript_json_path)
                            
                            with open(transcript_json_path, "a") as f:
                                if file_size > 3:
                                    f.write(',\n')
                                json.dump(transcription_data, f, indent=2)
                            
                            self.logger.info(f"Successfully transcribed: {text[:100]}...")
                            return text
                        else:
                            self.logger.warning("Transcription returned empty text")
                    else:
                        self.logger.error(f"Unexpected result format: {result}")
                else:
                    self.logger.error(f"Unexpected response type: {response.get('type')}")
                
                return ""
                
        except Exception as e:
            self.logger.error(f"Transcription error: {e}", exc_info=True)
            return ""

    async def _generate_enhanced_notes(self, text: str) -> Optional[Note]:
        """Generate enhanced notes from transcribed text using Cloudflare AI"""
        try:
            self.logger.debug("Generating enhanced notes...")
            context = "\n".join(
                f"{item['timestamp']}: {item['text']}" 
                for item in self.context_buffer[-3:]
            )
            
            async with await self._connect_websocket() as websocket:
                prompt = f"""
                Analyze the following transcript in the context of previous content:

                Previous context:
                {context}

                Current content:
                {text}

                Please provide a structured analysis including:
                1. A concise summary
                2. Category (choose from: information, action, decision, question, or discussion)
                3. Key points (if any)
                4. Important entities mentioned (people, organizations, concepts)
                5. Importance rating (1-5, where 5 is highest)

                Format your response as JSON with the following structure:
                {{
                    "summary": "...",
                    "category": "...",
                    "key_points": ["..."],
                    "entities": ["..."],
                    "importance": N
                }}
                """
                
                request = {
                    "type": "universal.create",
                    "request": {
                        "eventId": f"notes_{datetime.now().timestamp()}",
                        "provider": "workers-ai",
                        "endpoint": "@cf/meta/llama-2-7b-chat-int8",
                        "headers": {
                            "Authorization": f"Bearer {self.cf_token}",
                            "Content-Type": "application/json",
                        },
                        "query": {
                            "messages": [{"role": "user", "content": prompt}]
                        }
                    }
                }
                
                await websocket.send(json.dumps(request))
                response = json.loads(await websocket.recv())
                
                if response["type"] == "universal.created":
                    llm_response = response["response"]["result"]["response"]
                    
                    json_str = llm_response[llm_response.find('{'):llm_response.rfind('}')+1]
                    analysis = json.loads(json_str)
                    
                    return Note(
                        timestamp=datetime.now(),
                        content=analysis.get('summary', ''),
                        category=analysis.get('category', 'information'),
                        key_points=analysis.get('key_points', []),
                        entities=analysis.get('entities', []),
                        importance=analysis.get('importance', 1)
                    )
                return None
                
        except Exception as e:
            self.logger.error(f"Note generation error: {e}", exc_info=True)
            return None

    def _clean_transcript(self, new_text: str) -> str:
        """Clean up transcribed text by removing overlapping repeated phrases"""
        if not self.context_buffer:
            return new_text
            
        last_text = self.context_buffer[-1]['text'] if self.context_buffer else ""
        
        last_words = last_text.split()
        new_words = new_text.split()
        
        overlap_start = 0
        for i in range(min(len(last_words), len(new_words))):
            if last_words[-i-1:] == new_words[:i+1]:
                overlap_start = i + 1
        
        if overlap_start > 0:
            cleaned_words = new_words[overlap_start:]
            return " ".join(cleaned_words) if cleaned_words else new_text
            
        return new_text

async def main():
    try:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler()]
        )
        root_logger = logging.getLogger()
        root_logger.info("Starting application...")
        
        stream_url = "http://a.files.bbci.co.uk/media/live/manifesto/audio/simulcast/hls/nonuk/sbr_low/ak/bbc_world_service.m3u8"
        processor = StreamProcessor(stream_url, summary_interval_minutes=5)
        await processor.process_stream()
    except KeyboardInterrupt:
        root_logger.info("Application shutdown requested")
    except Exception as e:
        root_logger.error(f"Application error: {e}", exc_info=True)
    finally:
        root_logger.info("Application shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass