import json
import time
import jwt
import requests
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404
from google.oauth2 import service_account
from google.auth.transport.requests import Request

from .models import Employee
from .serializers import EmployeeSerializer

import qrcode
from io import BytesIO

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

import cloudinary
import cloudinary.uploader
from django.core.mail import send_mail

# Cloudinary config
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET
)

@csrf_exempt
@api_view(['POST'])
def create_employee(request):
    serializer = EmployeeSerializer(data=request.data)
    if serializer.is_valid():
        employee = serializer.save()

        # Generate and send wallet pass
        result = generate_and_send_wallet_pass(employee)

        return Response({
            'employee': serializer.data,
            'wallet_result': result
        }, status=status.HTTP_201_CREATED)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def generate_wallet_pass(request, emp_id):
    try:
        employee = get_object_or_404(Employee, emp_id=emp_id)
        result = generate_and_send_wallet_pass(employee)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def generate_and_send_wallet_pass(employee):
    try:
        # Load service account credentials
        with open(settings.GOOGLE_SERVICE_ACCOUNT_FILE) as f:
            service_account_info = json.load(f)

        private_key = service_account_info['private_key']
        client_email = service_account_info['client_email']

        issuer_id = settings.GOOGLE_WALLET_ISSUER_ID
        class_id = settings.GOOGLE_WALLET_CLASS_ID
        full_class_id = f"{issuer_id}.{class_id}"
        object_id = f"{issuer_id}.{employee.emp_id.replace(' ', '_')}"

        # Get access token
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/wallet_object.issuer']
        )
        credentials.refresh(Request())
        access_token = credentials.token
        headers = {"Authorization": f"Bearer {access_token}"}

        # Create class if not exists
        class_url = f"https://walletobjects.googleapis.com/walletobjects/v1/genericClass/{full_class_id}"
        class_check_response = requests.get(class_url, headers=headers)

        if class_check_response.status_code == 404:
            class_payload = {
                "id": full_class_id,
                "issuerName": "Convergint",
                "reviewStatus": "underReview",
                "hexBackgroundColor": "#FFC0CB",
                "logo": {
                    "sourceUri": {
                        "uri": settings.WALLET_LOGO_URI
                    }
                }
            }

            create_class_response = requests.post(
                "https://walletobjects.googleapis.com/walletobjects/v1/genericClass",
                headers={**headers, "Content-Type": "application/json"},
                json=class_payload
            )

            if create_class_response.status_code != 200:
                return {
                    "error": "Failed to create genericClass",
                    "details": create_class_response.json()
                }

        # Prepare text modules
        text_modules = [
            {'header': 'Name', 'body': employee.name},
            {'header': 'Employee ID', 'body': employee.emp_id},
            {'header': 'Phone', 'body': employee.phone},
            {'header': 'Email', 'body': employee.email}
        ]

        iat = int(time.time())
        exp = iat + 3600

        # Initial payload for QR code generation
        payload = {
            'iss': client_email,
            'aud': 'google',
            'typ': 'savetowallet',
            'iat': iat,
            'exp': exp,
            'payload': {
                'genericObjects': [{
                    'id': object_id,
                    'classId': full_class_id,
                    'cardTitle': {
                        'defaultValue': {
                            'language': 'en-US',
                            'value': employee.name
                        }
                    },
                    'header': {
                        'defaultValue': {
                            'language': 'en-US',
                            'value': employee.emp_id
                        }
                    },
                    'textModulesData': text_modules,
                    'heroImage': {
                        'sourceUri': {
                            'uri': settings.WALLET_DEFAULT_HERO_IMAGE
                        }
                    },
                    'imageModulesData': [{
                        'mainImage': {
                            'sourceUri': {
                                'uri': settings.WALLET_DEFAULT_HERO_IMAGE
                            },
                            'contentDescription': {
                                'defaultValue': {
                                    'language': 'en-US',
                                    'value': 'Convergint Logo'
                                }
                            }
                        }
                    }]
                }]
            }
        }

        signed_jwt = jwt.encode(payload, private_key, algorithm='RS256')
        save_url = f"https://pay.google.com/gp/v/save/{signed_jwt}"

        # Shorten URL
        api_url = f"https://tinyurl.com/api-create.php?url={save_url}"
        response = requests.get(api_url)
        short_url = response.text.strip() if response.status_code == 200 else save_url

        # Generate QR
        qr = qrcode.make(short_url)
        buffer = BytesIO()
        qr.save(buffer, format='PNG')
        buffer.seek(0)

        # Upload QR to Cloudinary
        upload_result = cloudinary.uploader.upload(
            buffer,
            folder="wallet_qr/",
            public_id=f"{employee.emp_id}_qr",
            overwrite=True,
            resource_type="image"
        )
        image_url = upload_result.get("secure_url")

        # Final payload with QR
        payload['payload']['genericObjects'][0]['imageModulesData'][0]['mainImage']['sourceUri']['uri'] = image_url

        signed_jwt = jwt.encode(payload, private_key, algorithm='RS256')
        save_url = f"https://pay.google.com/gp/v/save/{signed_jwt}"

        # Send email
        subject = 'Your Google Wallet Pass is Ready'
        message = f"""
        Hi {employee.name},

        Your digital visiting pass is ready. You can save it to your Google Wallet using the link below:

        {save_url}

        Thank you,
        Convergint Team
        """
        recipient_list = [employee.email]

        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_list)

        return {
            "status": "success",
            "short_url": short_url,
            "image_url": image_url,
            "email_sent": True
        }

    except Exception as e:
        return {"error": "Something went wrong", "details": str(e)}