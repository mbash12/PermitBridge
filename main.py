#!/usr/bin/env python3

import requests
import json
from datetime import datetime, timedelta
import pandas as pd
from zk import ZK
from rich.console import Console
from rich.progress import Progress
import sys
import logging
from rich.logging import RichHandler
import time

# Initialize Rich console
console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)]
)
logger = logging.getLogger("fingerprint")


class MyRequests:
    def __init__(self, base_url, api_key):
        self.base_url = base_url
        self.api_key = api_key
        self.headers = {
            'Content-Type': 'application/json',
            'X-API-KEY': api_key
        }

    def get_request(self, endpoint, params=None):
        url = self.base_url + endpoint
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {str(e)}")
            return None

    def post_request(self, endpoint, payload=None):
        if payload is None:
            logger.error("Error: Payload is empty")
            return None

        url = self.base_url + endpoint
        try:
            if isinstance(payload, list) and len(payload) > 100:
                return self._batch_post(url, payload)
            else:
                return self._single_post(url, payload)
        except Exception as e:
            logger.error(f"Error in post request: {str(e)}")
            return None

    def _batch_post(self, url, payload):
        batch_size = 100
        batches = [payload[i:i + batch_size] for i in range(0, len(payload), batch_size)]
        responses = []

        console.print("[cyan]Uploading data in batches...")
        for i, batch in enumerate(batches, 1):
            try:
                console.print(f"Processing batch {i}/{len(batches)}")
                json_payload = json.dumps(batch)
                response = requests.post(url, data=json_payload, headers=self.headers)
                response.raise_for_status()
                responses.append(response.json() if 'application/json' in response.headers.get('Content-Type',
                                                                                               '') else response.text)
            except requests.exceptions.RequestException as e:
                logger.error(f"Batch upload failed: {str(e)}")
                retry = input("Batch upload failed. Would you like to retry? (y/n): ")
                if retry.lower() != 'y':
                    break

        return responses

    def _single_post(self, url, payload):
        try:
            json_payload = json.dumps(payload)
            response = requests.post(url, data=json_payload, headers=self.headers)
            response.raise_for_status()
            return response.json() if 'application/json' in response.headers.get('Content-Type', '') else response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Upload failed: {str(e)}")
            return None


class FingerprintDevice:
    def __init__(self, ip_address, port=4370, timeout=20, password=0, force_udp=False):
        self.ip_address = ip_address
        self.port = port
        self.timeout = timeout
        self.password = password
        self.force_udp = force_udp
        self.zk = None

    def connect(self):
        try:
            self.zk = ZK(
                self.ip_address,
                port=self.port,
                timeout=self.timeout,
                password=self.password,
                force_udp=self.force_udp,
                ommit_ping=False
            )
            self.zk.connect()
            return True
        except Exception as e:
            logger.error(f"Connection failed: {str(e)}")
            return False

    def disconnect(self):
        if self.zk:
            self.zk.disconnect()

    def get_attendance_data(self, thedate=None):
        if not self.connect():
            return None

        try:
            console.print("[cyan]Fetching attendance data...")
            attendances = self.zk.get_attendance()
            response = {'log': [], 'clock': []}
            three_month_ago = datetime.now() - timedelta(days=90)

            for attendance in attendances:
                if attendance.timestamp >= three_month_ago:
                    response['log'].append({
                        'employee_id': int(attendance.user_id),
                        'timestamp': attendance.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                    })

            response['log'].sort(key=lambda x: x['timestamp'])
            df = pd.DataFrame(response['log'])
            if not df.empty:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                grouped_df = df.groupby(['employee_id', df['timestamp'].dt.date]).agg(
                    clock_in=('timestamp', 'min'),
                    clock_out=('timestamp', 'max')
                ).reset_index()

                for _, row in grouped_df.iterrows():
                    date = row['timestamp'].strftime('%Y-%m-%d')
                    if thedate is None or (
                            thedate.lower() == 'today' and date == datetime.today().strftime('%Y-%m-%d')):
                        response['clock'].append({
                            'employee_id': row['employee_id'],
                            'date': date,
                            'clock_in': row['clock_in'].strftime('%Y-%m-%d %H:%M:%S'),
                            'clock_out': row['clock_out'].strftime('%Y-%m-%d %H:%M:%S')
                        })

                response['clock'].sort(key=lambda x: x['date'])
            return response

        except Exception as e:
            logger.error(f"Error fetching attendance data: {str(e)}")
            return None
        finally:
            self.disconnect()

    def get_employee_data(self):
        if not self.connect():
            return None

        try:
            console.print("[cyan]Fetching employee data...")
            users = self.zk.get_users()
            return [{'id': user.user_id, 'name': user.name} for user in users]
        except Exception as e:
            logger.error(f"Error fetching employee data: {str(e)}")
            return None
        finally:
            self.disconnect()


class AttendanceSystem:
    def __init__(self, ip_address, api_base_url, api_key):
        self.device = FingerprintDevice(ip_address)
        self.api = MyRequests(api_base_url, api_key)
        self.api_v3 = MyRequests(api_base_url.replace('/v1', '/v3'), api_key)

    def clear_log(self):
        return self.api_v3.post_request('/clear-log', payload='')

    def clear_usr(self):
        return self.api_v3.post_request('/clear-usr', payload='')

    def upload_user(self):
        console.print("[cyan]Starting employee data upload...")
        data = self.device.get_employee_data()
        if not data:
            logger.error("Failed to retrieve employee data")
            return False

        existing_data = self.api.get_request('/records/employees')
        if not existing_data or 'records' not in existing_data:
            logger.error("Failed to retrieve existing employee records")
            return False

        existing_ids = set(str(record['id']) for record in existing_data['records'])
        new_employees = [emp for emp in data if str(emp['id']) not in existing_ids]

        if not new_employees:
            console.print("[yellow]No new employees to upload.")
            return True

        result = self.api.post_request('/records/employees', payload=new_employees)
        return result is not None

    def upload_log(self):
        console.print("[cyan]Starting attendance log upload...")
        data = self.device.get_attendance_data()
        if not data:
            logger.error("Failed to retrieve attendance data")
            return False

        self.clear_log()
        result = self.api.post_request('/records/clocks', payload=data['clock'])
        return result is not None


def get_settings():
    return {
        'IP_ADDRESS': '10.10.3.245',
        'API_BASE_URL': 'https://permithub.pelangiservice.com/api/v1',
        'API_KEY': 'GDb5Yd5P2t2qEXj5jx4R6XEy'
    }


def import_all(system):
    """Import both employee and attendance data"""
    console.print("\n[bold green]Starting full import process...")
    if system.upload_user() and system.upload_log():
        console.print("[green]Successfully imported all data!")
    else:
        console.print("[red]Import failed. Please check the logs and try again.")


def import_employee(system):
    """Import only employee data"""
    console.print("\n[bold green]Starting employee import process...")
    if system.upload_user():
        console.print("[green]Successfully imported employee data!")
    else:
        console.print("[red]Employee import failed. Please check the logs and try again.")


def import_attendance(system):
    """Import only attendance data"""
    console.print("\n[bold green]Starting attendance import process...")
    if system.upload_log():
        console.print("[green]Successfully imported attendance data!")
    else:
        console.print("[red]Attendance import failed. Please check the logs and try again.")


def show_menu():
    console.print("\n[bold cyan]Fingerprint Attendance System[/bold cyan]")
    console.print("\nPlease select an option:")
    console.print("1. Import All Data")
    console.print("2. Import Employee Data")
    console.print("3. Import Attendance Data")
    console.print("0. Exit")

    while True:
        try:
            choice = input("\nEnter your choice (0-3): ")
            if choice in ['0', '1', '2', '3']:
                return choice
            console.print("[red]Invalid choice. Please enter a number between 0 and 3.")
        except ValueError:
            console.print("[red]Invalid input. Please enter a number.")


def main():
    settings = get_settings()
    system = AttendanceSystem(settings['IP_ADDRESS'], settings['API_BASE_URL'], settings['API_KEY'])

    while True:
        choice = show_menu()

        if choice == '0':
            console.print("\n[yellow]Exiting program...")
            break
        elif choice == '1':
            import_all(system)
        elif choice == '2':
            import_employee(system)
        elif choice == '3':
            import_attendance(system)

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")
        sys.exit(1)