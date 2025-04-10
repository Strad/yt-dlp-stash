# ⚠ Don't use relative imports
from yt_dlp.postprocessor.common import PostProcessor
import stashapi.log as log
from stashapi.stashapp import StashInterface
from time import sleep
from pathlib import Path

# ℹ️ See the docstring of yt_dlp.postprocessor.common.PostProcessor

# ⚠ The class name must end in "PP"


class StashPP(PostProcessor):
    def __init__(self, downloader=None, scheme: str='http', host: str='localhost', port: int=9999, apikey: str='', sessioncookie: str='', searchpathoverride: str='', scrapemethod: str='yt_dlp', **kwargs):
        # ⚠ Only kwargs can be passed from the CLI, and all argument values will be string
        # Also, "downloader", "when" and "key" are reserved names
        super().__init__(downloader)
        self.tag = None
        self._kwargs = kwargs
        self.scrapemethod = scrapemethod
        stash_args = {
                "Scheme": scheme,
                "Host": host,
                "Port": port,
                "Logger": log
            }
        if apikey:
            stash_args["ApiKey"] = apikey
        elif sessioncookie:
            stash_args["SessionCookie"] = sessioncookie
        self.stash = StashInterface(stash_args)
        self.searchpathoverride = searchpathoverride

    def run(self, info):
        if self.scrapemethod == 'stash':
            # Updated logic uses Stash GraphQL API to update the scene
            return self.stash_scrape(info)
        return self.ytdlp_scrape(info)

    # ℹ️ See docstring of yt_dlp.postprocessor.common.PostProcessor.run
    def ytdlp_scrape(self, info):
        if self.searchpathoverride != '':
            filepath = (self.searchpathoverride + info['requested_downloads'][0]['filename'][1:]).replace("//","/")
            dirpath = "/".join(filepath.split("/")[0:-1])
        else:
            filepath = info['requested_downloads'][0]['filepath']
            dirpath = info['requested_downloads'][0]['__finaldir']
        self.to_screen("Scanning metadata on path: " + dirpath)
        try:
            stash_meta_job = self.stash.metadata_scan(paths=dirpath,flags={
                "scanGenerateCovers": False,
                "scanGeneratePreviews":False,
                "scanGenerateImagePreviews": False,
                "scanGenerateSprites": False,
                "scanGeneratePhashes":False,
                "scanGenerateThumbnails": False,
                "scanGenerateClipPreviews": False
            })
        except Exception as e:
            self.to_screen("Error on metadata scan: " + str(e))
            return [], info
        while self.stash.find_job(stash_meta_job)["status"] != "FINISHED":
            sleep(0.5)
        scene = self.stash.find_scenes({"path": {"modifier": "EQUALS", "value": filepath}})
        self.to_screen("Found scene with id: " + scene[0]["id"])
        self.tag = self.stash.find_tags({"name": {"modifier": "EQUALS", "value": "scrape"}})
        if len(self.tag) == 0:
            self.tag = [self.stash.create_tag({"name": "scrape"})]
        update_scene = {
            "id": scene[0]["id"],
            "title": info["title"],
            "url": info["webpage_url"],
            "tag_ids": [self.tag[0]["id"]],
            "cover_image": info["thumbnail"],
        }
        if "description" in info:
            update_scene["details"] = info["description"]
        if "upload_date" in info:
            update_scene["date"] = info["upload_date"][0:4] + "-" + info["upload_date"][4:6] + "-" + info["upload_date"][6:8]
        self.stash.update_scene(update_scene)
        self.to_screen("Updatet Scene")
        return [], info
    
    def stash_scrape(self, info):
        try:
            # Step 1: Determine the file path and directory
            if self.searchpathoverride != '':
                # Get the full relative path of the downloaded file
                full_download_path = Path(info['requested_downloads'][0]['filepath'])
                # Determine the correct root directory for relative path calculation
                download_root = Path(info['requested_downloads'][0]['__finaldir']).parent.parent
                relative_path = full_download_path.relative_to(download_root)

                # Create the new filepath by combining searchpathoverride with the relative path
                filepath = Path(self.searchpathoverride) / relative_path
            else:
                filepath = Path(info['requested_downloads'][0]['filepath'])

            # Convert the path to a string with forward slashes for compatibility
            filepath = str(filepath).replace("\\", "/")
            dirpath = str(Path(filepath).parent).replace("\\", "/")

            # Debugging: Print the file path and directory path
            self.to_screen(f"[Debug] Full download path: {full_download_path}")
            self.to_screen(f"[Debug] Download root: {download_root}")
            self.to_screen(f"[Debug] Relative path: {relative_path}")
            self.to_screen(f"[Debug] Filepath for metadata scan: {filepath}")
            self.to_screen(f"[Debug] Directory for metadata scan: {dirpath}")

            # Step 3: Metadata scan for the input file
            self.to_screen("[Debug] Initiating metadata scan on path: " + dirpath)
            stash_meta_job = self.stash.metadata_scan(paths=dirpath, flags={
                "scanGenerateCovers": False,
                "scanGeneratePreviews": False,
                "scanGenerateImagePreviews": False,
                "scanGenerateSprites": False,
                "scanGeneratePhashes": False,
                "scanGenerateThumbnails": False,
                "scanGenerateClipPreviews": False
            })
            self.to_screen(f"[Debug] Metadata scan job ID: {stash_meta_job}")

            # Step 4: Wait until metadata scan is complete
            while True:
                job_status = self.stash.find_job(stash_meta_job)
                self.to_screen(f"[Debug] Metadata scan job status: {job_status}")
                if job_status["status"] == "FINISHED":
                    break
                elif job_status["status"] == "FAILED":
                    self.to_screen("[Error] Metadata scan job failed.")
                    return [], info
                sleep(0.5)

            # Step 5: Find the newly created scene
            self.to_screen(f"[Debug] Looking for scene with path: {filepath}")
            scene = self.stash.find_scenes({"path": {"modifier": "EQUALS", "value": filepath}})
            self.to_screen(f"[Debug] Scene search result: {scene}")
            if not scene or len(scene) == 0:
                self.to_screen("[Error] No scene found after metadata scan. Please verify the path and metadata settings.")
                return [], info

            scene_id = scene[0]["id"]
            self.to_screen(f"[Debug] Found scene with id: {scene_id}")

            # Step 6: Scrape metadata from URL
            if "webpage_url" not in info:
                self.to_screen("[Error] No URL found for scraping")
                return [], info

            self.to_screen(f"[Debug] Scraping metadata from URL: {info['webpage_url']}")
            scene_data = self.scrape_scene_by_url(info['webpage_url'])

            if not scene_data:
                self.to_screen("[Error] Error or no data found during scraping.")
                return [], info

            # Step 7: Update the scene data using the scraped information
            update_scene = {
                "id": scene_id,
                "url": info.get("webpage_url"),
            }

            if scene_data.get("title"):
                update_scene["title"] = scene_data["title"]
            elif info.get("title"):
                update_scene["title"] = info["title"]

            if scene_data.get("details"):
                update_scene["details"] = scene_data["details"]
            elif info.get("description"):
                update_scene["details"] = info["description"]

            if scene_data.get("date"):
                update_scene["date"] = scene_data["date"]

            if scene_data.get("image"):
                update_scene["cover_image"] = scene_data["image"]
            elif info.get("thumbnail"):
                update_scene["cover_image"] = info["thumbnail"]

            if scene_data.get("tags"):
                update_scene["tags"] = [{"name": tag["name"]} for tag in scene_data["tags"]]

            # Step to handle performers
            if scene_data.get("performers"):
                performer_ids = []
                for performer in scene_data["performers"]:
                    performer_name = performer["name"]
                    performer_url = performer.get("url")
                    
                    # Search for existing performer
                    existing_performer = self.stash.find_performers({"name": {"modifier": "EQUALS", "value": performer_name}})
                    
                    if existing_performer and len(existing_performer) > 0:
                        performer_ids.append(existing_performer[0]["id"])
                    else:
                        # Create the performer if they don't exist
                        new_performer = {"name": performer_name}
                        if performer_url:
                            new_performer["url"] = performer_url
                        
                        created_performer = self.stash.create_performer(new_performer)
                        performer_ids.append(created_performer["id"])
                
                # Add performer IDs to the update payload
                update_scene["performer_ids"] = performer_ids

            if scene_data.get("studio"):
                studio_name = scene_data["studio"]["name"]
                existing_studio = self.stash.find_studios({"name": {"modifier": "EQUALS", "value": studio_name}})
                
                if not existing_studio:
                    existing_studio = self.stash.find_studios({"aliases": {"modifier": "EQUALS", "value": studio_name}})
                
                if existing_studio and len(existing_studio) > 0:
                    update_scene["studio_id"] = existing_studio[0]["id"]
                    self.to_screen(f"[Debug] using existing studio {existing_studio}")
                else:
                    # Create the studio if it doesn't exist
                    self.to_screen("[Debug] creating new studio")
                    new_studio = {
                        "name": studio_name,
                        "url": scene_data["studio"]["url"] if scene_data["studio"].get("url") else None
                    }
                    created_studio = self.stash.create_studio(new_studio)
                    update_scene["studio_id"] = created_studio["id"]

            self.to_screen(f"[Debug] Scene update payload: {update_scene}")

            try:
                self.stash.update_scene(update_scene)
                self.to_screen("[Debug] Scene updated with scraped metadata.")
            except Exception as e:
                self.to_screen(f"[Error] Error updating scene metadata: {str(e)}")

        except Exception as e:
            self.to_screen(f"[Error] Unexpected error during processing: {str(e)}")

        return [], info
    
    def scrape_scene_by_url(self, url):
        query = """
        query scrapeSceneByURL($url: String!) {
            scrapeSceneURL(url: $url) {
                title
                date
                details
                tags {
                    name
                }
                performers {
                    name
                    url
                }
                studio {
                    name
                    url
                }
            }
        }
        """
        variables = {
            "url": url
        }
        try:
            self.to_screen(f"[Debug] Sending GraphQL scrapeSceneByURL query for URL: {url}")
            response = self.stash.call_GQL(query, variables)
            self.to_screen(f"[Debug] Full GraphQL response: {response}")

            # Adjust the response check to accommodate both formats
            scrape_scene_data = None
            if 'data' in response and isinstance(response['data'], dict):
                scrape_scene_data = response['data'].get('scrapeSceneURL')
            elif 'scrapeSceneURL' in response:
                scrape_scene_data = response['scrapeSceneURL']

            if scrape_scene_data is None:
                self.to_screen("[Error] 'scrapeSceneURL' field missing or is None in GraphQL response.")
                self.to_screen(f"[Debug] Response structure: {response}")
                return None

            # Add detailed debug messages to understand what we received
            if scrape_scene_data:
                self.to_screen(f"[Debug] Scraped scene data: {scrape_scene_data}")
                return scrape_scene_data
            else:
                self.to_screen("[Error] GraphQL response contained 'scrapeSceneURL' but it was empty.")
                self.to_screen(f"[Debug] 'scrapeSceneURL' content: {scrape_scene_data}")
                return None
        except Exception as e:
            self.to_screen(f"[Error] Error in scraping scene by URL: {str(e)}")
            return None