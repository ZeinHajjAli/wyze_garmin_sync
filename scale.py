#!/usr/local/bin/python3
import hashlib
import math
import os
import time
from getpass import getpass
import threading
import logging
from typing import Dict

import garth
import schedule
from flask import Flask, jsonify
from wyze_sdk import Client
from wyze_sdk.errors import WyzeApiError

from fit import FitEncoder_Weight

WYZE_EMAIL = os.environ.get("WYZE_EMAIL", "")
WYZE_PASSWORD = os.environ.get("WYZE_PASSWORD", "")
WYZE_KEY_ID = os.environ.get("WYZE_KEY_ID", "")
WYZE_API_KEY = os.environ.get("WYZE_API_KEY", "")
GARMIN_USERNAME = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "5000"))

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Flask app for webhook
app = Flask(__name__)


def login_to_wyze():
    try:
        response = Client().login(
            email=WYZE_EMAIL,
            password=WYZE_PASSWORD,
            key_id=WYZE_KEY_ID,
            api_key=WYZE_API_KEY,
        )
        access_token = response.get("access_token")
        return access_token
    except WyzeApiError as e:
        print(f"Wyze API Error: {e}")
        return None


def upload_to_garmin(file_path):
    try:
        garth.resume("./.garmin_tokens")
        garth.client.username
    except Exception:
        try:
            garth.login(GARMIN_USERNAME, GARMIN_PASSWORD)
            garth.save("./.garmin_tokens")
        except Exception:
            email = input("Enter Garmin email address: ")
            password = getpass("Enter Garmin password: ")
            try:
                print("before login")
                garth.login(email, password)
                print("after login")
                garth.save("./.garmin_tokens")
                print("after save")
            except Exception as exc:
                print(repr(exc))
                exit()

    try:
        with open(file_path, "rb") as f:
            garth.client.upload(f)
        return True
    except Exception as e:
        print(f"Garmin upload error: {e}")
        return False


def generate_fit_file(scale):
    fit = FitEncoder_Weight()
    timestamp = math.trunc(scale.latest_records[0].measure_ts / 1000)
    weight_in_kg = scale.latest_records[0].weight * 0.45359237

    data_keys = {
        "percent_fat": scale.latest_records[0].body_fat,
        "percent_hydration": scale.latest_records[0].body_water,
        "visceral_fat_mass": scale.latest_records[0].body_vfr,
        "bone_mass": scale.latest_records[0].bone_mineral,
        "muscle_mass": scale.latest_records[0].muscle,
        "basal_met": scale.latest_records[0].bmr,
        "physique_rating": scale.latest_records[0].body_type or 5,
        "active_met": scale.latest_records[0].bmr,
        "metabolic_age": scale.latest_records[0].metabolic_age,
        "visceral_fat_rating": scale.latest_records[0].body_vfr,
        "bmi": scale.latest_records[0].bmi,
    }
    data = {}
    for key, value in data_keys.items():
        if value is not None:
            data[key] = float(value)
        else:
            data[key] = None
    if data.get("basal_met") is None:
        data["active_met"] = None
    else:
        data["active_met"] = int(float(scale.latest_records[0].bmr) * 1.25)
    fit.write_file_info(time_created=timestamp)
    fit.write_file_creator()
    fit.write_device_info(timestamp=timestamp)
    fit.write_weight_scale(
        timestamp=timestamp,
        weight=weight_in_kg,
        percent_fat=data.get("percent_fat"),
        percent_hydration=data.get("percent_hydration"),
        visceral_fat_mass=data.get("visceral_fat_mass"),
        bone_mass=data.get("bone_mass"),
        muscle_mass=data.get("muscle_mass"),
        basal_met=data.get("basal_met"),
        physique_rating=data.get("physique_rating"),
        active_met=data.get("active_met"),
        metabolic_age=data.get("metabolic_age"),
        visceral_fat_rating=data.get("visceral_fat_rating"),
        bmi=data.get("bmi"),
    )
    fit.finish()
    with open("wyze_scale.fit", "wb") as fitfile:
        fitfile.write(fit.getvalue())


def sync_data() -> Dict:
    """Main sync function that can be called from webhook or schedule"""
    try:
        logger.info("Starting Wyze to Garmin sync...")
        access_token = login_to_wyze()
        if access_token:
            client = Client(token=access_token)
            for device in client.devices_list():
                if device.type == "WyzeScale":
                    scale = client.scales.info(device_mac=device.mac)
                    if scale:
                        logger.info(
                            f"Scale found with MAC {device.mac}. Latest record is:"
                        )
                        logger.info(scale.latest_records)
                        logger.info(
                            f"Body Type: {scale.latest_records[0].body_type or 5}"
                        )

                        logger.info("Generating fit data...")
                        generate_fit_file(scale)
                        logger.info("Fit data generated...")

                        fitfile_path = "./wyze_scale.fit"
                        cksum_file_path = "./cksum.txt"

                        # Calculate checksum of the fit file
                        with open(fitfile_path, "rb") as fitfile:
                            cksum = hashlib.md5(fitfile.read()).hexdigest()

                        # Check if cksum.txt exists and read stored checksum
                        if os.path.exists(cksum_file_path):
                            with open(cksum_file_path, "r") as cksum_file:
                                stored_cksum = cksum_file.read().strip()

                            # Compare calculated checksum with stored checksum
                            if cksum == stored_cksum:
                                logger.info("No new measurement")
                                return {
                                    "status": "success",
                                    "message": "No new measurement to sync",
                                }
                            else:
                                logger.info(
                                    "New measurement detected. Uploading file..."
                                )
                                # Upload the fit file to Garmin
                                if upload_to_garmin(fitfile_path):
                                    logger.info("File uploaded successfully.")
                                    # Update cksum.txt with the new checksum
                                    with open(cksum_file_path, "w") as cksum_file:
                                        cksum_file.write(cksum)
                                    return {
                                        "status": "success",
                                        "message": "New measurement synced successfully",
                                    }
                                else:
                                    logger.error("File upload failed.")
                                    return {
                                        "status": "error",
                                        "message": "File upload failed",
                                    }
                        else:
                            logger.info(
                                "No chksum detected. Uploading fit file and creating chksum..."
                            )
                            # Upload the fit file to Garmin
                            if upload_to_garmin(fitfile_path):
                                logger.info("File uploaded successfully.")
                                # Create cksum.txt and write the checksum
                                with open(cksum_file_path, "w") as cksum_file:
                                    cksum_file.write(cksum)
                                logger.info("cksum.txt created.")
                                return {
                                    "status": "success",
                                    "message": "Initial sync completed successfully",
                                }
                            else:
                                logger.error("File upload failed.")
                                return {
                                    "status": "error",
                                    "message": "File upload failed",
                                }
        else:
            logger.error("Failed to login to Wyze")
            return {"status": "error", "message": "Failed to login to Wyze"}
    except Exception as e:
        logger.error(f"Sync failed with error: {str(e)}")
        return {"status": "error", "message": f"Sync failed: {str(e)}"}

    return {"status": "error", "message": "Sync failed"}


def main():
    """Legacy main function for backward compatibility"""
    result = sync_data()
    print(result["message"])


@app.route("/webhook/sync", methods=["POST", "GET"])
def webhook_sync():
    """Webhook endpoint to trigger sync on demand"""
    try:
        logger.info("Webhook triggered - starting sync...")
        result = sync_data()
        return jsonify(result), 200 if result["status"] == "success" else 500
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "message": "Wyze Garmin Sync is running"}), 200


def run_scheduler():
    """Run the scheduled tasks in a separate thread"""
    schedule.every().day.at("08:00").do(sync_data)

    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    logger.info("Starting Wyze Garmin Sync with webhook support...")

    # Run initial sync
    logger.info("Running initial sync...")
    sync_data()

    # Start scheduler in background thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("Background scheduler started")

    # Start Flask app
    logger.info(f"Starting webhook server on port {WEBHOOK_PORT}...")
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)
