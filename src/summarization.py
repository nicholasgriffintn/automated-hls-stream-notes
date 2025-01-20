import json
import logging
from datetime import datetime
from typing import List, Optional, TextIO

import config
from models import Note, IntervalSummary
from cloudflare import CloudflareService

class SummarizationService(CloudflareService):
    def __init__(self, logger: logging.Logger):
        super().__init__(logger)
    
    async def generate_enhanced_notes(self, text: str) -> Optional[Note]:
        """Generate enhanced notes from transcribed text using AI service"""
        if not text:
            return None
        
        prompt = f"""
        Analyze the following transcript:

        Content:
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

        Only provide the JSON response, no additional text before or after.
        """
        
        request = {
            "type": "universal.create",
            "request": {
                "eventId": f"notes_{datetime.now().timestamp()}",
                "provider": "workers-ai",
                "endpoint": "@cf/meta/llama-3-8b-instruct",
                "headers": {
                    "Authorization": f"Bearer {config.CF_TOKEN}",
                    "Content-Type": "application/json",
                },
                "query": {
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 512,
                    "temperature": 0.7,
                    "top_p": 0.9
                }
            }
        }
        
        response = await self._send_request(request)
        if not response:
            return None
        
        if response.get("type") == "universal.created":
            llm_response = response["response"]["result"]["response"]
            self.logger.debug(f"Raw LLM response: {llm_response[:200]}...")
            
            try:
                json_str = llm_response[llm_response.find('{'):llm_response.rfind('}')+1]
                analysis = json.loads(json_str)
                
                note = Note(
                    timestamp=datetime.now(),
                    content=str(analysis.get('summary', '')),
                    category=str(analysis.get('category', 'information')),
                    key_points=[str(kp) for kp in analysis.get('key_points', [])],
                    entities=[str(e) for e in analysis.get('entities', [])],
                    importance=max(1, min(5, int(analysis.get('importance', 1))))
                )
                return note
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
                self.logger.error(f"Failed to parse LLM response: {e}")
                return None
        
        self.logger.error(f"Unexpected response type: {response.get('type')}")
        return None
    
    async def generate_interval_summary(
        self,
        notes: List[Note],
        interval_file: TextIO,
        current_file: TextIO
    ) -> Optional[IntervalSummary]:
        """Generate a summary for the current interval using AI service"""
        if not notes:
            return None
        
        start_time = min(note.timestamp for note in notes)
        end_time = max(note.timestamp for note in notes)
        
        notes_text = "\n".join(
            f"Time: {note.timestamp}\n"
            f"Content: {note.content}\n"
            f"Category: {note.category}\n"
            f"Key Points: {', '.join(note.key_points)}\n"
            f"Entities: {', '.join(note.entities)}\n"
            f"Importance: {note.importance}\n"
            for note in notes
        )
        
        prompt = f"""
        Analyze these notes from the last interval:

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
        
        Only provide the JSON response, no additional text before or after.
        """
        
        request = {
            "type": "universal.create",
            "request": {
                "eventId": f"summary_{datetime.now().timestamp()}",
                "provider": "workers-ai",
                "endpoint": "@cf/meta/llama-3-8b-instruct",
                "headers": {
                    "Authorization": f"Bearer {config.CF_TOKEN}",
                    "Content-Type": "application/json",
                },
                "query": {
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024,
                    "temperature": 0.7,
                    "top_p": 0.9
                }
            }
        }
        
        response = await self._send_request(request)
        if not response:
            return None
        
        if response.get("type") == "universal.created":
            llm_response = response["response"]["result"]["response"]
            self.logger.debug(f"Raw LLM response: {llm_response[:200]}...")
            
            json_str = llm_response[llm_response.find('{'):llm_response.rfind('}')+1]
            analysis = json.loads(json_str)
            
            summary = IntervalSummary(
                start_time=start_time,
                end_time=end_time,
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
            
            return summary
        
        self.logger.error(f"Unexpected response type: {response.get('type')}")
        return None
    
    async def generate_final_report(
        self,
        summaries: List[IntervalSummary],
        report_file: TextIO
    ) -> None:
        """Generate a final report from all interval summaries"""
        if not summaries:
            report_file.write("# Stream Processing Report\n\n")
            report_file.write("No content was processed during this session.\n")
            return
        
        summaries_text = "\n".join(
            f"Period {i+1}: {summary.start_time} to {summary.end_time}\n" +
            f"Key Points: {', '.join(summary.key_points)}\n" +
            f"Main Topics: {', '.join(summary.main_topics)}\n" +
            f"Important Events: {', '.join(summary.important_events)}\n" +
            f"Action Items: {', '.join(summary.action_items)}\n"
            for i, summary in enumerate(summaries)
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
                "endpoint": "@cf/meta/llama-3-8b-instruct",
                "headers": {
                    "Authorization": f"Bearer {config.CF_TOKEN}",
                    "Content-Type": "application/json",
                },
                "query": {
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2048,
                    "temperature": 0.7,
                    "top_p": 0.9
                }
            }
        }
        
        response = await self._send_request(request)
        if not response:
            report_file.write("\n\nError: Failed to generate final report.\n")
            return
        
        if response.get("type") == "universal.created":
            report_file.write(response["response"]["result"]["response"])
            report_file.flush()
            return
        
        self.logger.error(f"Unexpected response type: {response.get('type')}")
        report_file.write("\n\nError: Failed to generate final report due to unexpected response type.\n") 