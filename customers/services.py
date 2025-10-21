# customers/services.py
import os
import json
import requests
from typing import List, Dict, Optional, Any
from django.utils import timezone
from datetime import datetime

from .models import Customer
from companies.models import Company
from project.settings_qbo import BASE_URL, QBO_ENVIRONMENT, QBO_CLIENT_ID, QBO_REDIRECT_URI_FRONTEND



class QuickBooksCustomerService:
    """Service to manage customer operations with QuickBooks API"""

    def __init__(self, company: Company):
        self.company = company
        if not company.is_connected:
            raise ValueError("Company is not connected to QuickBooks")

    def get_headers(self) -> Dict[str, str]:
        """Get authorization headers for QB API"""
        return {
            'Authorization': f'Bearer {self.company.access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

    # ---------------------------
    # Utility: clean payload
    # ---------------------------
    def _remove_empty_fields(self, data: Any) -> Any:
        """
        Recursively remove empty strings, None, empty dicts and empty lists.
        Keeps booleans and numeric zero values.
        """
        if isinstance(data, dict):
            cleaned = {}
            for k, v in data.items():
                cleaned_v = self._remove_empty_fields(v)
                # keep booleans and numbers (including 0). drop "", None, {}, []
                if cleaned_v is "" or cleaned_v is None:
                    continue
                if isinstance(cleaned_v, dict) and not cleaned_v:
                    continue
                if isinstance(cleaned_v, list) and not cleaned_v:
                    continue
                cleaned[k] = cleaned_v
            return cleaned
        elif isinstance(data, list):
            cleaned_list = []
            for item in data:
                cleaned_item = self._remove_empty_fields(item)
                if cleaned_item is "" or cleaned_item is None:
                    continue
                if isinstance(cleaned_item, dict) and not cleaned_item:
                    continue
                if isinstance(cleaned_item, list) and not cleaned_item:
                    continue
                cleaned_list.append(cleaned_item)
            return cleaned_list
        else:
            return data

    # ---------------------------
    # Fetch customers (with pagination)
    # ---------------------------
    def fetch_customers_from_qb(self) -> List[Dict[str, Any]]:
        """Fetch all customers from QuickBooks API (handles pagination using STARTPOSITION/MAXRESULTS)"""
        url = f"{BASE_URL}/v3/company/{self.company.realm_id}/query"
        all_customers: List[Dict[str, Any]] = []
        start_position = 1
        batch_size = 1000

        while True:
            query = f"SELECT * FROM Customer STARTPOSITION {start_position} MAXRESULTS {batch_size}"
            print(f"🔹 Fetching customers {start_position}–{start_position + batch_size - 1}")

            response = requests.get(
                url,
                headers=self.get_headers(),
                params={'query': query},
                timeout=30
            )

            # handle token expiry / unauthorized
            if response.status_code == 401:
                raise Exception("QuickBooks access token expired or unauthorized (401). Refresh token required.")

            response.raise_for_status()

            data = response.json()
            customers = data.get("QueryResponse", {}).get("Customer", [])

            if not customers:
                break

            all_customers.extend(customers)
            print(f"✅ Retrieved {len(customers)} customers in this batch (total so far: {len(all_customers)})")

            if len(customers) < batch_size:
                break

            start_position += batch_size

        print(f"🎯 Finished fetching {len(all_customers)} total customers.")
        return all_customers

    # ---------------------------
    # Sync single customer to DB
    # ---------------------------
    def sync_customer_to_db(self, customer_data: Dict) -> Customer:
        """Sync single customer to the local database (create or update)"""
        bill_addr = customer_data.get('BillAddr', {}) or {}
        ship_addr = customer_data.get('ShipAddr', {}) or {}

        customer, created = Customer.objects.update_or_create(
            company=self.company,
            qb_customer_id=customer_data['Id'],
            defaults={
                'display_name': customer_data.get('DisplayName', ''),
                'given_name': customer_data.get('GivenName'),
                'family_name': customer_data.get('FamilyName'),
                'company_name': customer_data.get('CompanyName'),
                'email': (customer_data.get('PrimaryEmailAddr') or {}).get('Address'),
                'phone': (customer_data.get('PrimaryPhone') or {}).get('FreeFormNumber'),
                'mobile': (customer_data.get('Mobile') or {}).get('FreeFormNumber'),
                'fax': (customer_data.get('Fax') or {}).get('FreeFormNumber'),
                'website': (customer_data.get('WebAddr') or {}).get('URI'),

                # Billing Address
                'bill_addr_line1': bill_addr.get('Line1'),
                'bill_addr_line2': bill_addr.get('Line2'),
                'bill_addr_city': bill_addr.get('City'),
                'bill_addr_state': bill_addr.get('CountrySubDivisionCode'),
                'bill_addr_postal_code': bill_addr.get('PostalCode'),
                'bill_addr_country': bill_addr.get('Country'),

                # Shipping Address
                'ship_addr_line1': ship_addr.get('Line1'),
                'ship_addr_line2': ship_addr.get('Line2'),
                'ship_addr_city': ship_addr.get('City'),
                'ship_addr_state': ship_addr.get('CountrySubDivisionCode'),
                'ship_addr_postal_code': ship_addr.get('PostalCode'),
                'ship_addr_country': ship_addr.get('Country'),

                # Financial Info
                'balance': customer_data.get('Balance', 0),
                'balance_with_jobs': customer_data.get('BalanceWithJobs', 0),

                # Status and Metadata
                'active': customer_data.get('Active', True),
                'sync_token': customer_data.get('SyncToken', '0'),
                'notes': customer_data.get('Notes'),

                # Tax Info
                'taxable': customer_data.get('Taxable', True),
                'tax_code_ref_value': (customer_data.get('TaxCodeRef') or {}).get('value'),
                'tax_code_ref_name': (customer_data.get('TaxCodeRef') or {}).get('name'),

                'raw_data': customer_data
            }
        )

        print(f"✅ Synced customer {customer.display_name} ({'created' if created else 'updated'})")
        return customer

    def sync_all_customers(self) -> int:
        """Fetch all customers from QB and sync them into the DB. Returns number synced."""
        customers_data = self.fetch_customers_from_qb()
        synced_count = 0

        for customer_data in customers_data:
            self.sync_customer_to_db(customer_data)
            synced_count += 1

        return synced_count

    # ---------------------------
    # Create customer in QuickBooks
    # ---------------------------
    def create_customer_in_qb(self, customer_data: Dict) -> Dict[str, Any]:
        """Create a new customer in QuickBooks and return the created customer object."""
        url = f"{BASE_URL}/v3/company/{self.company.realm_id}/customer"

        qb_customer: Dict[str, Any] = {
            "DisplayName": customer_data.get('display_name'),
        }

        # optional fields - only add keys if values exist (we will clean later)
        if customer_data.get('given_name'):
            qb_customer["GivenName"] = customer_data['given_name']
        if customer_data.get('family_name'):
            qb_customer["FamilyName"] = customer_data['family_name']
        if customer_data.get('company_name'):
            qb_customer["CompanyName"] = customer_data['company_name']

        # Contact info
        if customer_data.get('email') is not None:
            qb_customer["PrimaryEmailAddr"] = {"Address": customer_data.get('email')}
        if customer_data.get('phone') is not None:
            qb_customer["PrimaryPhone"] = {"FreeFormNumber": customer_data.get('phone')}
        if customer_data.get('mobile') is not None:
            qb_customer["Mobile"] = {"FreeFormNumber": customer_data.get('mobile')}
        if customer_data.get('fax') is not None:
            qb_customer["Fax"] = {"FreeFormNumber": customer_data.get('fax')}
        if customer_data.get('website') is not None:
            qb_customer["WebAddr"] = {"URI": customer_data.get('website')}

        # Status
        qb_customer["Active"] = customer_data.get('active', True)
        qb_customer["Taxable"] = customer_data.get('taxable', True)

        if customer_data.get('notes'):
            qb_customer["Notes"] = customer_data['notes']

        # Billing address
        bill_addr_fields = [
            'bill_addr_line1', 'bill_addr_line2', 'bill_addr_city',
            'bill_addr_state', 'bill_addr_postal_code', 'bill_addr_country'
        ]
        if any(customer_data.get(field) for field in bill_addr_fields):
            bill_addr = {}
            if customer_data.get('bill_addr_line1'):
                bill_addr["Line1"] = customer_data['bill_addr_line1']
            if customer_data.get('bill_addr_line2'):
                bill_addr["Line2"] = customer_data['bill_addr_line2']
            if customer_data.get('bill_addr_city'):
                bill_addr["City"] = customer_data['bill_addr_city']
            if customer_data.get('bill_addr_state'):
                bill_addr["CountrySubDivisionCode"] = customer_data['bill_addr_state']
            if customer_data.get('bill_addr_postal_code'):
                bill_addr["PostalCode"] = customer_data['bill_addr_postal_code']
            if customer_data.get('bill_addr_country'):
                bill_addr["Country"] = customer_data['bill_addr_country']
            qb_customer["BillAddr"] = bill_addr

        # Shipping address
        ship_addr_fields = [
            'ship_addr_line1', 'ship_addr_line2', 'ship_addr_city',
            'ship_addr_state', 'ship_addr_postal_code', 'ship_addr_country'
        ]
        if any(customer_data.get(field) for field in ship_addr_fields):
            ship_addr = {}
            if customer_data.get('ship_addr_line1'):
                ship_addr["Line1"] = customer_data['ship_addr_line1']
            if customer_data.get('ship_addr_line2'):
                ship_addr["Line2"] = customer_data['ship_addr_line2']
            if customer_data.get('ship_addr_city'):
                ship_addr["City"] = customer_data['ship_addr_city']
            if customer_data.get('ship_addr_state'):
                ship_addr["CountrySubDivisionCode"] = customer_data['ship_addr_state']
            if customer_data.get('ship_addr_postal_code'):
                ship_addr["PostalCode"] = customer_data['ship_addr_postal_code']
            if customer_data.get('ship_addr_country'):
                ship_addr["Country"] = customer_data['ship_addr_country']
            qb_customer["ShipAddr"] = ship_addr

        # Tax code
        if customer_data.get('tax_code_ref_value'):
            qb_customer["TaxCodeRef"] = {"value": customer_data['tax_code_ref_value']}

        # Clean payload
        qb_customer = self._remove_empty_fields(qb_customer)

        print(f"🔧 Creating customer in QuickBooks: {json.dumps(qb_customer, indent=2)}")

        response = requests.post(
            url,
            headers=self.get_headers(),
            json=qb_customer,
            timeout=30
        )

        print(f"📡 QuickBooks API Response Status: {response.status_code}")

        if response.status_code == 401:
            raise Exception("QuickBooks unauthorized (401). Access token likely expired.")

        # QuickBooks create returns 200 on success (sandbox). Some accounts return 201 - treat 2xx as success.
        if not (200 <= response.status_code < 300):
            error_text = response.text
            print(f"❌ QuickBooks API Error {response.status_code}: {error_text}")
            try:
                error_data = response.json()
                error_message = error_data.get('Fault', {}).get('Error', [{}])[0].get('Message', 'Unknown error')
                error_detail = error_data.get('Fault', {}).get('Error', [{}])[0].get('Detail', '')
                raise Exception(f"QuickBooks API Error: {error_message} - {error_detail}")
            except ValueError:
                raise Exception(f"QuickBooks API returned {response.status_code}: {error_text}")

        result = response.json()
        created_customer = result.get('Customer') or result.get('Customer', {})

        if not created_customer:
            raise ValueError("No Customer object returned from QuickBooks create response")

        print(f"✅ Created customer in QuickBooks: {created_customer.get('DisplayName')} (ID: {created_customer.get('Id')})")
        return created_customer

    # ---------------------------
    # Update customer in QuickBooks
    # ---------------------------
    def update_customer_in_qb(self, customer: Customer, update_data: Dict) -> Dict[str, Any]:
        """Update an existing customer in QuickBooks with proper API format"""
        url = f"{BASE_URL}/v3/company/{self.company.realm_id}/customer"

        # Build the QuickBooks customer object - use exact QB field names
        qb_customer: Dict[str, Any] = {
            "Id": customer.qb_customer_id,
            "SyncToken": customer.sync_token,
            "DisplayName": update_data.get('display_name', customer.display_name),
            "sparse": True  # required for partial updates
        }

        # Names and company
        if 'given_name' in update_data:
            qb_customer["GivenName"] = update_data.get('given_name') or ""
        if 'family_name' in update_data:
            qb_customer["FamilyName"] = update_data.get('family_name') or ""
        if 'company_name' in update_data:
            qb_customer["CompanyName"] = update_data.get('company_name') or ""

        # Status fields
        if 'active' in update_data:
            qb_customer["Active"] = update_data['active']
        if 'taxable' in update_data:
            qb_customer["Taxable"] = update_data['taxable']

        # Notes
        if 'notes' in update_data:
            qb_customer["Notes"] = update_data.get('notes') or ""

        # Contact information
        if 'email' in update_data:
            qb_customer["PrimaryEmailAddr"] = {"Address": update_data.get('email') or ""}
        if 'phone' in update_data:
            qb_customer["PrimaryPhone"] = {"FreeFormNumber": update_data.get('phone') or ""}
        if 'mobile' in update_data:
            qb_customer["Mobile"] = {"FreeFormNumber": update_data.get('mobile') or ""}
        if 'fax' in update_data:
            qb_customer["Fax"] = {"FreeFormNumber": update_data.get('fax') or ""}
        if 'website' in update_data:
            qb_customer["WebAddr"] = {"URI": update_data.get('website') or ""}

        # Billing address update
        bill_addr_updated = any(field in update_data for field in [
            'bill_addr_line1', 'bill_addr_line2', 'bill_addr_city',
            'bill_addr_state', 'bill_addr_postal_code', 'bill_addr_country'
        ])
        if bill_addr_updated:
            bill_addr: Dict[str, Any] = {}
            if 'bill_addr_line1' in update_data:
                bill_addr["Line1"] = update_data.get('bill_addr_line1') or ""
            if 'bill_addr_line2' in update_data:
                bill_addr["Line2"] = update_data.get('bill_addr_line2') or ""
            if 'bill_addr_city' in update_data:
                bill_addr["City"] = update_data.get('bill_addr_city') or ""
            if 'bill_addr_state' in update_data:
                bill_addr["CountrySubDivisionCode"] = update_data.get('bill_addr_state') or ""
            if 'bill_addr_postal_code' in update_data:
                bill_addr["PostalCode"] = update_data.get('bill_addr_postal_code') or ""
            if 'bill_addr_country' in update_data:
                bill_addr["Country"] = update_data.get('bill_addr_country') or ""
            qb_customer["BillAddr"] = bill_addr

        # Shipping address update
        ship_addr_updated = any(field in update_data for field in [
            'ship_addr_line1', 'ship_addr_line2', 'ship_addr_city',
            'ship_addr_state', 'ship_addr_postal_code', 'ship_addr_country'
        ])
        if ship_addr_updated:
            ship_addr: Dict[str, Any] = {}
            if 'ship_addr_line1' in update_data:
                ship_addr["Line1"] = update_data.get('ship_addr_line1') or ""
            if 'ship_addr_line2' in update_data:
                ship_addr["Line2"] = update_data.get('ship_addr_line2') or ""
            if 'ship_addr_city' in update_data:
                ship_addr["City"] = update_data.get('ship_addr_city') or ""
            if 'ship_addr_state' in update_data:
                ship_addr["CountrySubDivisionCode"] = update_data.get('ship_addr_state') or ""
            if 'ship_addr_postal_code' in update_data:
                ship_addr["PostalCode"] = update_data.get('ship_addr_postal_code') or ""
            if 'ship_addr_country' in update_data:
                ship_addr["Country"] = update_data.get('ship_addr_country') or ""
            qb_customer["ShipAddr"] = ship_addr

        # Tax code reference
        if 'tax_code_ref_value' in update_data:
            qb_customer["TaxCodeRef"] = {"value": update_data.get('tax_code_ref_value') or ""}

        # Clean up the payload to remove empty nested objects/fields
        qb_customer = self._remove_empty_fields(qb_customer)

        print(f"🔧 Sending to QuickBooks API: {json.dumps(qb_customer, indent=2)}")

        try:
            response = requests.post(
                url,
                headers=self.get_headers(),
                json=qb_customer,
                timeout=30
            )

            print(f"📡 QuickBooks API Response Status: {response.status_code}")

            if response.status_code == 401:
                # Let caller handle token refresh
                raise Exception("QuickBooks unauthorized (401). Access token likely expired.")

            if response.status_code != 200:
                error_text = response.text
                print(f"❌ QuickBooks API Error {response.status_code}: {error_text}")
                try:
                    error_data = response.json()
                    err = error_data.get('Fault', {}).get('Error', [{}])[0]
                    error_message = err.get('Message', 'Unknown error')
                    error_detail = err.get('Detail', '')
                    raise Exception(f"QuickBooks API Error: {error_message} - {error_detail}")
                except ValueError:
                    raise Exception(f"QuickBooks API returned {response.status_code}: {error_text}")

            response_data = response.json()
            print(f"✅ QuickBooks API Success")

            updated_customer = response_data.get('Customer')
            if not updated_customer:
                raise ValueError("No customer data in QuickBooks response")

            print(f"✅ Updated customer in QuickBooks: {updated_customer.get('DisplayName')}")
            return updated_customer

        except requests.RequestException as e:
            print(f"❌ Network error: {str(e)}")
            raise Exception(f"Failed to connect to QuickBooks: {str(e)}")
        except Exception as e:
            print(f"❌ Error updating customer: {str(e)}")
            raise

    # ---------------------------
    # Helper to update local customer from QB response
    # ---------------------------
    def update_customer_from_qb_data(self, customer: Customer, qb_data: Dict) -> Customer:
        """Update local customer model from QuickBooks response data (keeps existing fields if not present)."""
        customer.sync_token = qb_data.get('SyncToken', customer.sync_token)

        # Update addresses
        bill_addr = qb_data.get('BillAddr', {}) or {}
        if bill_addr:
            customer.bill_addr_line1 = bill_addr.get('Line1', customer.bill_addr_line1)
            customer.bill_addr_line2 = bill_addr.get('Line2', customer.bill_addr_line2)
            customer.bill_addr_city = bill_addr.get('City', customer.bill_addr_city)
            customer.bill_addr_state = bill_addr.get('CountrySubDivisionCode', customer.bill_addr_state)
            customer.bill_addr_postal_code = bill_addr.get('PostalCode', customer.bill_addr_postal_code)
            customer.bill_addr_country = bill_addr.get('Country', customer.bill_addr_country)

        ship_addr = qb_data.get('ShipAddr', {}) or {}
        if ship_addr:
            customer.ship_addr_line1 = ship_addr.get('Line1', customer.ship_addr_line1)
            customer.ship_addr_line2 = ship_addr.get('Line2', customer.ship_addr_line2)
            customer.ship_addr_city = ship_addr.get('City', customer.ship_addr_city)
            customer.ship_addr_state = ship_addr.get('CountrySubDivisionCode', customer.ship_addr_state)
            customer.ship_addr_postal_code = ship_addr.get('PostalCode', customer.ship_addr_postal_code)
            customer.ship_addr_country = ship_addr.get('Country', customer.ship_addr_country)

        # Basic fields
        if qb_data.get('DisplayName'):
            customer.display_name = qb_data.get('DisplayName')
        if qb_data.get('GivenName') is not None:
            customer.given_name = qb_data.get('GivenName')
        if qb_data.get('FamilyName') is not None:
            customer.family_name = qb_data.get('FamilyName')
        if qb_data.get('CompanyName') is not None:
            customer.company_name = qb_data.get('CompanyName')
        if (qb_data.get('PrimaryEmailAddr') or {}).get('Address') is not None:
            customer.email = (qb_data.get('PrimaryEmailAddr') or {}).get('Address')
        if (qb_data.get('PrimaryPhone') or {}).get('FreeFormNumber') is not None:
            customer.phone = (qb_data.get('PrimaryPhone') or {}).get('FreeFormNumber')

        return customer

    # ---------------------------
    # Public sync/create/update entrypoints used by views
    # ---------------------------
    def create_or_sync_customer(self, customer_payload: Dict) -> Customer:
        """
        Create customer in QuickBooks then create local model.
        This function assumes `customer_payload` conforms to your serializer.
        """
        qb_customer_data = self.create_customer_in_qb(customer_payload)
        # create local DB entry (preserve provided fields)
        customer = Customer.objects.create(
            company=self.company,
            qb_customer_id=qb_customer_data['Id'],
            sync_token=qb_customer_data.get('SyncToken', '0'),
            raw_data=qb_customer_data,
            display_name=customer_payload.get('display_name') or qb_customer_data.get('DisplayName'),
            given_name=customer_payload.get('given_name'),
            family_name=customer_payload.get('family_name'),
            company_name=customer_payload.get('company_name'),
            email=customer_payload.get('email'),
            phone=customer_payload.get('phone'),
            mobile=customer_payload.get('mobile'),
            fax=customer_payload.get('fax'),
            website=customer_payload.get('website'),
            notes=customer_payload.get('notes'),
            taxable=customer_payload.get('taxable', True),
            active=customer_payload.get('active', True)
        )
        return customer

    def update_and_sync_customer(self, customer: Customer, update_payload: Dict) -> Customer:
        """
        Update customer in QuickBooks, then update local model with QB response and payload fields.
        Returns updated local Customer instance.
        """
        qb_customer_data = self.update_customer_in_qb(customer, update_payload)
        # update model from QB response
        customer = self.update_customer_from_qb_data(customer, qb_customer_data)
        # also apply any fields from the payload that QB may not have returned
        for field, value in update_payload.items():
            if hasattr(customer, field):
                try:
                    setattr(customer, field, value)
                except Exception:
                    # ignore any assignment errors for non-model fields
                    pass
        customer.raw_data = qb_customer_data
        customer.save()
        return customer
