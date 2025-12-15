from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('db/edit/<int:pk>/', views.db_edit, name='db_edit'),
    path('db/delete/<int:pk>/', views.db_delete, name='db_delete'),
    path('ajax/db-search/', views.ajax_db_search, name='ajax_db_search'),
]