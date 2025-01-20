import asyncio
from dotenv import load_dotenv
import sys

from processor import StreamProcessor
import config

async def main():
    """Main entry point for the stream processing application"""
    load_dotenv()
    
    try:
        config.validate_config()
    except ValueError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)
    
    if len(sys.argv) < 2:
        print("Usage: uv run src/main.py <stream_url> [output_dir] [summary_interval_minutes]")
        sys.exit(1)
    
    stream_url = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else config.DEFAULT_OUTPUT_DIR
    summary_interval = int(sys.argv[3]) if len(sys.argv) > 3 else config.DEFAULT_SUMMARY_INTERVAL_MINUTES
    
    processor = StreamProcessor(
        stream_url=stream_url,
        output_dir=output_dir,
        summary_interval_minutes=summary_interval
    )
    
    current_task = asyncio.current_task()
    if current_task:
        current_task._processor = processor
    
    await processor.process_stream()
    return processor

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    processor = None
    
    try:
        main_task = loop.create_task(main())
        try:
            processor = loop.run_until_complete(main_task)
        except KeyboardInterrupt:
            print("\nShutdown requested... Running cleanup")
            try:
                processor = loop.run_until_complete(asyncio.wait_for(main_task, timeout=1.0))
            except (asyncio.TimeoutError, Exception) as e:
                if hasattr(main_task, '_processor'):
                    processor = main_task._processor
                print(f"Note: Cleanup may be incomplete due to: {str(e)}")
            
            if processor and processor.all_interval_summaries:
                print("Generating final report...")
                report_path = processor.subdirs['reports'] / "final_report.md"
                with open(report_path, 'w', encoding='utf-8') as report_file:
                    cleanup_task = loop.create_task(
                        processor.summarization_service.generate_final_report(
                            processor.all_interval_summaries,
                            report_file
                        )
                    )
                    loop.run_until_complete(cleanup_task)
            else:
                print("No interval summaries available to generate final report")
            
            main_task.cancel()
            try:
                loop.run_until_complete(main_task)
            except asyncio.CancelledError:
                pass
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        
        if pending:
            print("Cleaning up pending tasks...")
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        
        if processor and processor.file_handles:
            print("Closing file handles...")
            for fh in processor.file_handles.values():
                fh.close()
        
        loop.close() 