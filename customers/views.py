from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.db.models import Q
from typing import Dict, Any

from .models import Customer
from .serializers import CustomerSerializer, CustomerCreateUpdateSerializer
from .services import QuickBooksCustomerService
from companies.models import Company, ActiveCompany


class CustomerViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing customers with QuickBooks sync.
    """
    permission_classes = [IsAuthenticated]

    def get_active_company(self):
        """Get the user's active company"""
        try:
            active_company = ActiveCompany.objects.get(user=self.request.user)
            return active_company.company
        except ActiveCompany.DoesNotExist:
            try:
                membership = self.request.user.company_memberships.filter(is_default=True).first()
                return membership.company if membership else None
            except:
                return None

    def get_queryset(self):
        """Filter customers by the user's active company"""
        active_company = self.get_active_company()
        if not active_company:
            return Customer.objects.none()
        
        queryset = Customer.objects.filter(company=active_company).order_by('display_name')
        
        # Apply search filter
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(display_name__icontains=search) |
                Q(company_name__icontains=search) |
                Q(email__icontains=search) |
                Q(given_name__icontains=search) |
                Q(family_name__icontains=search)
            )
        
        # Apply active filter
        active_filter = self.request.query_params.get("active")
        if active_filter is not None:
            if active_filter.lower() in ['true', '1', 'yes']:
                queryset = queryset.filter(active=True)
            elif active_filter.lower() in ['false', '0', 'no']:
                queryset = queryset.filter(active=False)
        
        # Apply stub filter
        stub_filter = self.request.query_params.get("is_stub")
        if stub_filter is not None:
            if stub_filter.lower() in ['true', '1', 'yes']:
                queryset = queryset.filter(is_stub=True)
            elif stub_filter.lower() in ['false', '0', 'no']:
                queryset = queryset.filter(is_stub=False)
        
        return queryset

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return CustomerCreateUpdateSerializer
        return CustomerSerializer

    def list(self, request, *args, **kwargs):
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found. Please select a company first."
                }, status=status.HTTP_400_BAD_REQUEST)

            queryset = self.get_queryset()
            
            # Get customer statistics
            total_customers = queryset.count()
            stub_customers = queryset.filter(is_stub=True).count()
            active_customers = queryset.filter(active=True).count()
            
            # Pagination
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 20))
            
            paginator = Paginator(queryset, page_size)
            page_obj = paginator.get_page(page)
            
            serializer = self.get_serializer(page_obj, many=True)
            
            return Response({
                "success": True,
                "customers": serializer.data,
                "pagination": {
                    "count": paginator.count,
                    "next": page_obj.next_page_number() if page_obj.has_next() else None,
                    "previous": page_obj.previous_page_number() if page_obj.has_previous() else None,
                    "page_size": page_size,
                    "current_page": page,
                    "total_pages": paginator.num_pages
                },
                "company_info": {
                    "name": active_company.name,
                    "qb_company_name": active_company.qb_company_name,
                    "currency_code": active_company.currency_code,
                },
                "stats": {
                    "total_customers": total_customers,
                    "stub_customers": stub_customers,
                    "active_customers": active_customers,
                    "real_customers": total_customers - stub_customers
                }
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def create(self, request, *args, **kwargs):
        """Create customer and sync to QuickBooks"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found."
                }, status=status.HTTP_400_BAD_REQUEST)

            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            
            # Create customer in QuickBooks first
            service = QuickBooksCustomerService(active_company)
            qb_customer_data = service.create_customer_in_qb(serializer.validated_data)
            
            # Then create in local database
            customer = Customer.objects.create(
                company=active_company,
                qb_customer_id=qb_customer_data['Id'],
                sync_token=qb_customer_data['SyncToken'],
                raw_data=qb_customer_data,
                is_stub=False,  # Explicitly mark as real customer
                **serializer.validated_data
            )
            
            response_serializer = CustomerSerializer(customer)
            
            return Response({
                "success": True,
                "message": "Customer created successfully and synced to QuickBooks",
                "customer": response_serializer.data
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def update(self, request, *args, **kwargs):
        """Update customer and sync to QuickBooks"""
        try:
            customer = self.get_object()
            active_company = self.get_active_company()
            
            serializer = self.get_serializer(customer, data=request.data, partial=kwargs.get('partial', False))
            serializer.is_valid(raise_exception=True)
            
            # Update customer in QuickBooks first
            service = QuickBooksCustomerService(active_company)
            
            try:
                qb_customer_data = service.update_customer_in_qb(customer, serializer.validated_data)
                
                # Update local customer with data from QuickBooks response
                customer = self.update_customer_from_qb_data(customer, qb_customer_data)
                
                # Also update with the form data that might not be in QB response
                for field, value in serializer.validated_data.items():
                    if hasattr(customer, field):
                        setattr(customer, field, value)
                
                # If updating a stub customer, mark it as real
                if customer.is_stub:
                    customer.is_stub = False
                
                customer.save()
                
            except Exception as qb_error:
                return Response({
                    "success": False,
                    "error": f"QuickBooks update failed: {str(qb_error)}"
                }, status=status.HTTP_400_BAD_REQUEST)
            
            response_serializer = CustomerSerializer(customer)
            
            return Response({
                "success": True,
                "message": "Customer updated successfully and synced to QuickBooks",
                "customer": response_serializer.data
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def update_customer_from_qb_data(self, customer: Customer, qb_data: Dict) -> Customer:
        """Update customer model from QuickBooks data"""
        customer.sync_token = qb_data.get('SyncToken', customer.sync_token)
        
        # Update addresses from QB response
        bill_addr = qb_data.get('BillAddr', {})
        if bill_addr:
            customer.bill_addr_line1 = bill_addr.get('Line1', customer.bill_addr_line1)
            customer.bill_addr_line2 = bill_addr.get('Line2', customer.bill_addr_line2)
            customer.bill_addr_city = bill_addr.get('City', customer.bill_addr_city)
            customer.bill_addr_state = bill_addr.get('CountrySubDivisionCode', customer.bill_addr_state)
            customer.bill_addr_postal_code = bill_addr.get('PostalCode', customer.bill_addr_postal_code)
            customer.bill_addr_country = bill_addr.get('Country', customer.bill_addr_country)
        
        ship_addr = qb_data.get('ShipAddr', {})
        if ship_addr:
            customer.ship_addr_line1 = ship_addr.get('Line1', customer.ship_addr_line1)
            customer.ship_addr_line2 = ship_addr.get('Line2', customer.ship_addr_line2)
            customer.ship_addr_city = ship_addr.get('City', customer.ship_addr_city)
            customer.ship_addr_state = ship_addr.get('CountrySubDivisionCode', customer.ship_addr_state)
            customer.ship_addr_postal_code = ship_addr.get('PostalCode', customer.ship_addr_postal_code)
            customer.ship_addr_country = ship_addr.get('Country', customer.ship_addr_country)
        
        return customer
    
    @action(detail=False, methods=["post"], url_path="sync", url_name="sync-customers")
    def sync_from_qbo(self, request):
        """Trigger customer sync from QuickBooks API"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found. Please select a company first."
                }, status=status.HTTP_400_BAD_REQUEST)

            service = QuickBooksCustomerService(active_company)
            success_count, failed_count = service.sync_all_customers()
            
            # Get stub customer count
            stub_count = Customer.objects.filter(company=active_company, is_stub=True).count()
            
            return Response({
                "success": True,
                "message": f"✅ Synced {success_count} customers successfully. {failed_count} failed.",
                "synced_count": success_count,
                "failed_count": failed_count,
                "stub_customers": stub_count
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["post"], url_path="enhance-stubs", url_name="enhance-stub-customers")
    def enhance_stub_customers(self, request):
        """Enhance stub customers with real data from QuickBooks"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found. Please select a company first."
                }, status=status.HTTP_400_BAD_REQUEST)

            service = QuickBooksCustomerService(active_company)
            enhanced_count, failed_count = service.enhance_stub_customers()
            
            return Response({
                "success": True,
                "message": f"✅ Enhanced {enhanced_count} stub customers. {failed_count} failed.",
                "enhanced_count": enhanced_count,
                "failed_count": failed_count
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=["get"], url_path="stats", url_name="customer-stats")
    def customer_statistics(self, request):
        """Get customer statistics and quality metrics"""
        try:
            active_company = self.get_active_company()
            if not active_company:
                return Response({
                    "success": False,
                    "error": "No active company found."
                }, status=status.HTTP_400_BAD_REQUEST)

            # Customer stats
            total_customers = Customer.objects.filter(company=active_company).count()
            stub_customers = Customer.objects.filter(company=active_company, is_stub=True).count()
            active_customers = Customer.objects.filter(company=active_company, active=True).count()
            
            # Invoice relationship stats
            from invoices.models import Invoice
            total_invoices = Invoice.objects.filter(company=active_company).count()
            invoices_with_customers = Invoice.objects.filter(
                company=active_company, 
                customer__isnull=False
            ).count()
            invoices_with_stub_customers = Invoice.objects.filter(
                company=active_company,
                customer__is_stub=True
            ).count()
            
            return Response({
                "success": True,
                "stats": {
                    "customers": {
                        "total": total_customers,
                        "stub": stub_customers,
                        "real": total_customers - stub_customers,
                        "active": active_customers,
                        "inactive": total_customers - active_customers,
                        "quality_score": ((total_customers - stub_customers) / total_customers * 100) if total_customers > 0 else 0
                    },
                    "invoices": {
                        "total": total_invoices,
                        "with_customers": invoices_with_customers,
                        "with_stub_customers": invoices_with_stub_customers,
                        "without_customers": total_invoices - invoices_with_customers,
                        "link_quality_score": (invoices_with_customers / total_invoices * 100) if total_invoices > 0 else 0
                    }
                }
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["post"], url_path="enhance", url_name="enhance-customer")
    def enhance_single_customer(self, request, pk=None):
        """Enhance a single stub customer with real QuickBooks data"""
        try:
            customer = self.get_object()
            
            if not customer.is_stub:
                return Response({
                    "success": False,
                    "error": "Customer is not a stub and doesn't need enhancement"
                }, status=status.HTTP_400_BAD_REQUEST)
            
            active_company = self.get_active_company()
            service = QuickBooksCustomerService(active_company)
            
            # Fetch real customer data from QuickBooks
            real_customer_data = service.fetch_customer_from_qb(customer.qb_customer_id)
            if not real_customer_data:
                return Response({
                    "success": False,
                    "error": "Customer not found in QuickBooks"
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Update the customer with real data
            enhanced_customer = service.sync_customer_to_db(real_customer_data)
            
            response_serializer = CustomerSerializer(enhanced_customer)
            
            return Response({
                "success": True,
                "message": "Customer enhanced successfully with real QuickBooks data",
                "customer": response_serializer.data
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["get"], url_path="invoices", url_name="customer-invoices")
    def customer_invoices(self, request, pk=None):
        """Get invoices for a specific customer"""
        try:
            from invoices.models import Invoice
            from invoices.serializers import InvoiceSerializer
            
            customer = self.get_object()
            
            # Get both directly linked invoices and invoices by QB reference
            invoices = Invoice.objects.filter(
                Q(company=customer.company) &
                (Q(customer=customer) | Q(customer_ref_value=customer.qb_customer_id))
            ).order_by('-txn_date')
            
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 10))
            
            paginator = Paginator(invoices, page_size)
            page_obj = paginator.get_page(page)
            
            serializer = InvoiceSerializer(page_obj, many=True)
            
            return Response({
                "success": True,
                "invoices": serializer.data,
                "customer": {
                    "id": customer.id,
                    "display_name": customer.display_name,
                    "is_stub": customer.is_stub,
                    "qb_customer_id": customer.qb_customer_id
                },
                "pagination": {
                    "count": paginator.count,
                    "next": page_obj.next_page_number() if page_obj.has_next() else None,
                    "previous": page_obj.previous_page_number() if page_obj.has_previous() else None,
                    "page_size": page_size,
                    "current_page": page,
                    "total_pages": paginator.num_pages
                }
            })
            
        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)