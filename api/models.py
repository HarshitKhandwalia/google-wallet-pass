from django.db import models


class Employee(models.Model):
    emp_id = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    email = models.EmailField()
    
    def __str__(self):
        return f"{self.emp_id} - {self.name}"