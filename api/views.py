from rest_framework.views import APIView
from rest_framework.response import Response
from .serializers import MessageSerializer

class HelloAPIView(APIView):
    def get(self, request):
        data = {'message': 'Hello from the Django REST API!'}
        serializer = MessageSerializer(data)
        return Response(serializer.data)

