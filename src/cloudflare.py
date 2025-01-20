import websockets
import logging
from datetime import datetime
import asyncio
import json

import config

class CloudflareService:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._websocket = None
        self._last_connect_attempt = None
        self._connect_retry_delay = 5
    
    async def __aenter__(self):
        """Async context manager entry"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensures websocket is properly closed"""
        await self.disconnect()
    
    async def _connect_websocket(self) -> None:
        """Helper method to create websocket connection with proper headers"""
        try:
            if self._websocket:
                try:
                    pong = await self._websocket.ping()
                    await pong
                    return
                except Exception:
                    self.logger.debug("Ping failed, disconnecting...")
                    await self.disconnect()
            
            now = datetime.now()
            if self._last_connect_attempt and (now - self._last_connect_attempt).total_seconds() < self._connect_retry_delay:
                wait_time = self._connect_retry_delay - (now - self._last_connect_attempt).total_seconds()
                if wait_time > 0:
                    self.logger.debug(f"Waiting {wait_time:.1f}s before connecting...")
                    await asyncio.sleep(wait_time)
            
            self._last_connect_attempt = datetime.now()
            self.logger.debug("Connecting to websocket...")
            
            headers = {
                "cf-aig-authorization": f"Bearer {config.CF_TOKEN}",
                "Authorization": f"Bearer {config.CF_TOKEN}"
            }
            self._websocket = await websockets.connect(
                f"{config.CF_ENDPOINT}/{config.CF_ACCOUNT_ID}/{config.CF_GATEWAY_ID}",
                additional_headers=headers
            )
            self.logger.debug("Successfully connected to websocket")
            
        except Exception as e:
            self.logger.error(f"Failed to connect to websocket: {e}")
            await self.disconnect()
            raise
    
    async def disconnect(self) -> None:
        """Close the websocket connection"""
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception as e:
                self.logger.error(f"Error closing websocket: {e}")
            self._websocket = None
    
    async def _send_request(self, request: dict, retries: int = 3) -> dict:
        """Send a request to Cloudflare AI and get the response"""
        attempt = 0
        while attempt < retries:
            attempt += 1
            try:
                await self._connect_websocket()
                
                if not self._websocket:
                    raise ConnectionError("Failed to establish websocket connection")
                
                self.logger.debug(f"Sending request: {str(request)[:200]}...")
                await self._websocket.send(json.dumps(request))
                
                raw_response = await self._websocket.recv()
                self.logger.debug(f"Received raw response: {raw_response}")
                
                response = json.loads(raw_response)
                self.logger.debug(f"Parsed response: {str(response)[:200]}...")
                
                if response.get("type") == "error":
                    raise RuntimeError(f"API returned error: {response.get('error', {}).get('message', 'Unknown error')}")
                
                return response
                
            except Exception as e:
                self.logger.error(f"Error during request: {e}")
                await self.disconnect()
                
                if attempt < retries:
                    self.logger.debug(f"Retrying in {self._connect_retry_delay}s...")
                    await asyncio.sleep(self._connect_retry_delay)
                else:
                    self.logger.error("All retry attempts failed")
        
        return None
