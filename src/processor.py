import asyncio
import ffmpeg
import json
from datetime import datetime, timedelta
from typing import List, Set, TextIO, Optional, Dict, Any

import config
from models import Note, IntervalSummary
from utils import setup_logger, setup_output_directories, setup_output_files, save_metadata
from transcription import TranscriptionService
from summarization import SummarizationService

class StreamProcessor:
    def __init__(
        self,
        stream_url: str,
        output_dir: str = config.DEFAULT_OUTPUT_DIR,
        summary_interval_minutes: int = config.DEFAULT_SUMMARY_INTERVAL_MINUTES
    ):
        self.output_dir, self.subdirs = setup_output_directories(
            output_dir,
            stream_url,
            config.SUBDIRS
        )
        
        self.logger = setup_logger(
            f"StreamProcessor_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            self.subdirs['logs']
        )
        
        self.stream_url = stream_url
        self.summary_interval = timedelta(minutes=summary_interval_minutes)
        
        files = setup_output_files(self.output_dir, self.subdirs)
        self.transcription_service = TranscriptionService(
            self.logger,
            transcript_json_path=files['transcript_json']
        )
        self.summarization_service = SummarizationService(self.logger)
        
        self.current_interval_notes: List[Note] = []
        self.all_interval_summaries: List[IntervalSummary] = []
        self.last_summary_time: Optional[datetime] = None
        self.context_buffer = []
        self.empty_chunk_count = 0
        self.processed_transcripts: Set[str] = set()
        self.pending_summary_tasks: List[asyncio.Task] = []
        self.file_handles: Optional[Dict[str, TextIO]] = None
        
        save_metadata(self.output_dir, stream_url, summary_interval_minutes)
    
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
                    ar=str(config.SAMPLE_RATE),
                    loglevel='error',
                    bufsize='2M'
                )
                .overwrite_output()
                .run_async(pipe_stdout=True, pipe_stderr=True)
            )
            
            self.logger.info("ffmpeg process started successfully")
            
            files = setup_output_files(self.output_dir, self.subdirs)
            self.last_summary_time = datetime.now()
            
            with open(files['transcript'], 'w') as transcript_file, \
                 open(files['interval_summaries'], 'w') as interval_file, \
                 open(files['current_summary'], 'w') as current_file, \
                 open(files['final_report'], 'w') as report_file:
                
                self.file_handles = {
                    'transcript': transcript_file,
                    'interval_summaries': interval_file,
                    'current_summary': current_file,
                    'final_report': report_file
                }
                
                await self._process_audio_stream(
                    process,
                    transcript_file,
                    interval_file,
                    current_file
                )
                
                if self.pending_summary_tasks:
                    self.logger.info("Waiting for pending summary tasks to complete...")
                    await asyncio.gather(*self.pending_summary_tasks)
                
                await self.summarization_service.generate_final_report(
                    self.all_interval_summaries,
                    report_file
                )
            
        except Exception as e:
            self.logger.error(f"Error in processing loop: {e}", exc_info=True)
        finally:
            if process:
                process.kill()
            
            if self.file_handles:
                for handle in self.file_handles.values():
                    if not handle.closed:
                        handle.close()
    
    async def _process_audio_stream(
        self,
        process: Any,
        transcript_file: TextIO,
        interval_file: TextIO,
        current_file: TextIO
    ):
        """Process the audio stream and generate transcripts and summaries"""
        audio_buffer = bytearray()
        
        try:
            while True:
                try:
                    loop = asyncio.get_event_loop()
                    audio_chunk = await loop.run_in_executor(
                        None,
                        process.stdout.read,
                        config.CHUNK_SIZE
                    )
                    
                    self.logger.info(f"Read {len(audio_chunk)} bytes from ffmpeg")
                    
                    if not audio_chunk:
                        self.empty_chunk_count += 1
                        self.logger.warning(
                            f"No audio data received ({self.empty_chunk_count}/"
                            f"{config.MAX_EMPTY_CHUNKS})"
                        )
                        if self.empty_chunk_count >= config.MAX_EMPTY_CHUNKS:
                            self.logger.error(
                                f"No audio data received for {config.MAX_EMPTY_CHUNKS} "
                                "consecutive chunks, stopping..."
                            )
                            break
                        await asyncio.sleep(5)
                        continue
                    
                    self.empty_chunk_count = 0
                    audio_buffer.extend(audio_chunk)
                    
                    current_time = datetime.now()
                    
                    if len(audio_buffer) >= config.CHUNK_SIZE:
                        result = await self.transcription_service.transcribe_audio(
                            bytes(audio_buffer)
                        )
                        
                        if result:
                            transcription, timestamp = result
                            if transcription not in self.processed_transcripts:
                                self.logger.debug(f"Transcribed text: {transcription[:100]}...")
                                
                                if not self.context_buffer or transcription != self.context_buffer[-1]['text']:
                                    self.context_buffer.append({
                                        'timestamp': timestamp,
                                        'text': transcription
                                    })
                                    self.context_buffer = self.context_buffer[-config.CONTEXT_WINDOW_SIZE:]
                                    
                                    transcript_file.write(f"{timestamp}: {transcription}\n")
                                    transcript_file.flush()
                                    
                                    note = await self.summarization_service.generate_enhanced_notes(
                                        transcription
                                    )
                                    if note:
                                        self.current_interval_notes.append(note)
                                    self.processed_transcripts.add(transcription)
                            else:
                                self.logger.info("Skipping duplicate transcription")
                        else:
                            self.logger.error("Nothing was returned from the transcription service")
                        
                        audio_buffer = audio_buffer[-config.OVERLAP_SIZE:]
                    
                    if (current_time - self.last_summary_time >= self.summary_interval
                        and self.current_interval_notes):
                        
                        notes_to_summarize = self.current_interval_notes.copy()
                        self.current_interval_notes = []
                        
                        summary = await self.summarization_service.generate_interval_summary(
                            notes_to_summarize,
                            interval_file,
                            current_file
                        )
                        
                        if summary:
                            self.all_interval_summaries.append(summary)
                        
                        self.last_summary_time = current_time
                    
                except Exception as e:
                    self.logger.error(f"Error in processing loop: {e}")
                    break
            
            if self.context_buffer:
                self.logger.info("Processing remaining transcripts...")
                new_transcripts = [
                    transcript for transcript in self.context_buffer 
                    if transcript['text'] not in self.processed_transcripts
                ]
                
                for transcript in new_transcripts:
                    note = await self.summarization_service.generate_enhanced_notes(
                        transcript['text']
                    )
                    if note:
                        self.current_interval_notes.append(note)
                    self.processed_transcripts.add(transcript['text'])
                
                if self.current_interval_notes:
                    notes_to_summarize = self.current_interval_notes.copy()
                    self.current_interval_notes = []
                    
                    summary = await self.summarization_service.generate_interval_summary(
                        notes_to_summarize,
                        interval_file,
                        current_file
                    )
                    
                    if summary:
                        self.all_interval_summaries.append(summary) 
        finally:
            self.transcription_service.close()
    