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
    
    async def _connect_websocket(self) -> None:
        """Helper method to create websocket connection with proper headers"""
        try:
            if self._websocket:
                try:
                    pong = await self._websocket.ping()
                    await pong
                    return
                except Exception:
                    await self.disconnect()
            
            now = datetime.now()
            if self._last_connect_attempt and (now - self._last_connect_attempt).total_seconds() < self._connect_retry_delay:
                self.logger.debug("Waiting before retry...")
                return
            
            self._last_connect_attempt = now
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
        while retries > 0:
            try:
                await self._connect_websocket()
                
                if not self._websocket:
                    self.logger.error("Failed to establish websocket connection")
                    return None
                
                self.logger.debug(f"Sending request: {str(request)[:200]}...")
                await self._websocket.send(json.dumps(request))
                
                raw_response = await self._websocket.recv()
                self.logger.debug(f"Received raw response: {raw_response}")
                
                try:
                    response = json.loads(raw_response)
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse response JSON: {e}")
                    await self.disconnect()
                    return None
                
                self.logger.debug(f"Parsed response: {str(response)[:200]}...")
                
                if response.get("type") == "error":
                    self.logger.error(f"API returned error: {response.get('error', {}).get('message', 'Unknown error')}")
                    await self.disconnect()
                    return None
                
                await self.disconnect()
                return response
                
            except Exception as e:
                self.logger.error(f"Error during request (attempt {4-retries}/3): {e}")
                await self.disconnect()
                retries -= 1
                if retries > 0:
                    self.logger.debug(f"Retrying request... {retries} attempts remaining")
                    await asyncio.sleep(self._connect_retry_delay)
        
        return None
