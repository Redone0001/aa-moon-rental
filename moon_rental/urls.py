from django.urls import path

from moon_rental import views

app_name = "moon_rental"
urlpatterns = [
    path("", views.index, name="index"),
    path("administration/", views.admin_overview, name="admin_overview"),
    path("administration/invoices/", views.invoice_ledger, name="invoice_ledger"),
    path("moons/", views.moon_list, name="moons"),
    path("moons/export/", views.moon_export, name="moon_export"),
    path("moons/<int:moon_id>/", views.moon_detail, name="moon"),
    path("my-moons/", views.my_moons, name="my_moons"),
    path("auctions/new/", views.create_auction, name="create_auction"),
    path("auctions/<int:auction_id>/", views.auction_detail, name="auction"),
    path("lots/<int:lot_id>/bid/", views.bid, name="bid"),
    path("rentals/", views.rental_list, name="rentals"),
    path("rentals/assign/", views.assign, name="assign"),
    path("rentals/<int:rental_id>/end/", views.end_assignment, name="end_assignment"),
    path("invoices/", views.invoice_list, name="invoices"),
    path("invoices/<int:invoice_id>/void/", views.void_invoice, name="void_invoice"),
    path("wallets/", views.wallet_list, name="wallets"),
    path("wallets/character/", views.connect_character, name="connect_character"),
    path("wallets/corporation/", views.connect_corporation, name="connect_corporation"),
    path("wallets/<int:account_id>/", views.wallet_action, name="wallet_action"),
    path("sync/", views.sync, name="sync"),
]
