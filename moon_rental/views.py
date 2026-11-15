from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from esi.decorators import token_required

from moon_rental import forms, tasks
from moon_rental.filters import MoonFilters
from moon_rental.models import Auction, AuctionLot, Bid, Invoice, Moon, Rental, WalletAccount
from moon_rental.permissions import (
    is_admin,
    is_manager,
    require_access,
    require_admin,
    require_manager,
)
from moon_rental.services.rentals import assign_moon, audit, end_rental, open_auction, place_bid
from moon_rental.services.sync import sync_moons
from moon_rental.services.wallets import (
    SCOPES,
    connect_wallet,
    default_wallet,
    set_default_wallet,
    toggle_wallet,
)


def access(check=require_access):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            check(request.user)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def page(request, template, **context):
    return render(
        request,
        f"moon_rental/{template}.html",
        {
            "rental_manager": is_manager(request.user),
            "rental_admin": is_admin(request.user),
            "default_wallet": default_wallet(),
            "now": timezone.now(),
            **context,
        },
    )


@access()
def index(request):
    auctions = Auction.objects.filter(settled_at=None).prefetch_related("lots__moon")
    rentals = Rental.objects.filter(
        renter=request.user, active_moon__isnull=False, ends_on__gt=timezone.now().date()
    )
    invoices = Invoice.objects.filter(
        rental__renter=request.user, status=Invoice.Status.OPEN
    ).prefetch_related("receipts")
    return page(
        request,
        "index",
        auctions=auctions,
        my_count=rentals.count(),
        open_count=invoices.count(),
        due=sum(invoice.outstanding for invoice in invoices),
        auction_count=auctions.filter(ends_at__gt=timezone.now()).count(),
    )


@access()
def moon_list(request):
    moons = Moon.objects.select_related("current_rental__renter", "reserved_lot__auction")
    query = request.GET.get("q", "").strip()[:150]
    if query:
        moons = moons.filter(
            Q(name__icontains=query) | Q(region__icontains=query) | Q(solar_system__icontains=query)
        )
    return page(
        request, "moons", moons=Paginator(moons, 40).get_page(request.GET.get("page")), query=query
    )


@access()
def moon_detail(request, moon_id):
    moon = get_object_or_404(Moon, pk=moon_id)
    lots = moon.lots.select_related("auction").filter(auction__settled_at=None)
    return page(request, "moon_detail", moon=moon, lots=lots)


@access()
def auction_detail(request, auction_id):
    auction = get_object_or_404(Auction, pk=auction_id)
    moons = Moon.objects.filter(lots__auction=auction)
    filters = MoonFilters(request.GET, moons=moons)
    highest = Bid.objects.filter(lot=OuterRef("pk")).order_by("-amount", "created_at", "pk")
    lots = (
        auction.lots.filter(moon__in=filters.apply(moons))
        .select_related("moon")
        .annotate(bid_count=Count("bids"), top_id=Subquery(highest.values("pk")[:1]))
        .order_by("moon__name", "pk")
    )
    lot_page = Paginator(lots, 24).get_page(request.GET.get("page"))
    # Fetch only the highest bids on this page, rather than every bid in the auction.
    top_bids = Bid.objects.select_related("bidder").in_bulk(
        lot.top_id for lot in lot_page if lot.top_id is not None
    )
    rows = [
        {
            "lot": lot,
            "top": top_bids.get(lot.top_id),
            "count": lot.bid_count,
        }
        for lot in lot_page
    ]
    return page(
        request,
        "auction",
        auction=auction,
        rows=rows,
        filters=filters,
        lots=lot_page,
        bidding_open=auction.settled_at is None and auction.ends_at > timezone.now(),
    )


@access()
@require_POST
def bid(request, lot_id):
    lot = get_object_or_404(AuctionLot, pk=lot_id)
    form = forms.BidForm(request.POST)
    if form.is_valid():
        try:
            place_bid(actor=request.user, lot_id=lot.pk, **form.cleaned_data)
            messages.success(request, "Your bid is now the highest bid.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, "Enter a positive bid with at most two decimal places.")
    return redirect(reverse("moon_rental:auction", args=[lot.auction_id], query=request.GET))


def moon_selection(request, form_class, field_name):
    """Filter/page POSTs keep terms and choices without attempting to create a lease."""
    browsing = request.method == "POST" and (
        request.POST.get("action") in {"filter", "clear_filters", "select_page", "clear_selection"}
        or "moon_page" in request.POST
        or "remove_moon" in request.POST
    )
    form = form_class(request.POST if request.method == "POST" and not browsing else None)
    if browsing:
        form.initial = {
            name: field.widget.value_from_datadict(request.POST, request.FILES, name)
            for name, field in form.fields.items()
        }
    moons = form.fields[field_name].queryset
    filter_data = request.POST if request.method == "POST" else request.GET
    if request.POST.get("action") == "clear_filters":
        filter_data = {}
    filters = MoonFilters(filter_data, moons=moons)
    choices = Paginator(filters.apply(moons), 30).get_page(
        request.POST.get("moon_page") if request.method == "POST" else request.GET.get("page")
    )
    # Materialize only this page: MySQL cannot use a sliced queryset in an IN subquery.
    visible_moons = list(choices.object_list)
    form.fields[field_name].widget.moons = visible_moons
    values = form[field_name].value()
    if field_name == "moon":
        values = [values] if values else []
    ids = [
        int(pk)
        for pk in values or []
        if str(pk).isascii() and str(pk).isdigit() and len(str(pk)) < 20
    ]
    return (
        form,
        not browsing,
        {
            "filters": filters,
            "moon_choices": choices,
            "selection_name": field_name,
            "selected_moons": moons.filter(pk__in=ids).exclude(
                pk__in=[moon.pk for moon in visible_moons]
            )[:10],
            "selection_count": len(ids),
        },
    )


@access(require_manager)
@timezone.override("UTC")
def create_auction(request):
    form, saving, selection = moon_selection(request, forms.AuctionForm, "moons")
    if request.method == "POST" and saving and form.is_valid():
        data = form.cleaned_data.copy()
        data["moon_ids"] = list(data.pop("moons").values_list("pk", flat=True))
        try:
            auction = open_auction(actor=request.user, **data)
            return redirect("moon_rental:auction", auction_id=auction.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return page(
        request,
        "form",
        form=form,
        **selection,
        title="Open a moon auction",
        submit="Open bidding",
        help_text="Select available moons and set the terms. Each moon is awarded independently to its highest bidder.",
    )


@access(require_manager)
def assign(request):
    form, saving, selection = moon_selection(request, forms.AssignmentForm, "moon")
    if request.method == "POST" and saving and form.is_valid():
        data = form.cleaned_data.copy()
        data["moon_id"] = data.pop("moon").pk
        try:
            assign_moon(actor=request.user, **data)
            messages.success(request, "Moon assigned. The rental terms have been recorded.")
            return redirect("moon_rental:rentals")
        except ValidationError as exc:
            form.add_error(None, exc)
    return page(
        request,
        "form",
        form=form,
        **selection,
        title="Assign an available moon",
        submit="Assign moon",
        help_text="Existing assignments and moons reserved in an auction cannot be changed here.",
    )


@access()
def my_moons(request):
    rentals = (
        Rental.objects.filter(renter=request.user)
        .select_related("moon", "auction_lot__auction")
        .order_by("-starts_on")
    )
    return page(
        request,
        "rentals",
        rentals=Paginator(rentals, 40).get_page(request.GET.get("page")),
        title="My moons",
    )


@access(require_manager)
def rental_list(request):
    rentals = Rental.objects.select_related("moon", "renter", "auction_lot__auction").all()
    return page(
        request,
        "rentals",
        rentals=Paginator(rentals, 40).get_page(request.GET.get("page")),
        title="Rental assignments",
    )


@access(require_admin)
def end_assignment(request, rental_id):
    rental = get_object_or_404(Rental.objects.select_related("moon", "renter"), pk=rental_id)
    form = forms.ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        end_rental(actor=request.user, rental_id=rental.pk, **form.cleaned_data)
        messages.success(
            request,
            "Assignment ended. You can now assign a replacement renter and terms. Existing invoices remain payable.",
        )
        return redirect("moon_rental:assign")
    return page(
        request,
        "form",
        form=form,
        title=f"End or correct {rental.moon.name}",
        submit="End assignment",
        help_text=f"Current renter: {rental.renter}. This ends the existing lease. Then assign the moon again to change its renter or terms. Existing invoices remain payable unless separately voided by an admin.",
    )


@access()
def invoice_list(request):
    invoices = Invoice.objects.select_related("rental__moon", "rental__renter").prefetch_related(
        "receipts"
    )
    if not is_manager(request.user):
        invoices = invoices.filter(rental__renter=request.user)
    status = request.GET.get("status", "open")
    if status in Invoice.Status.values:
        invoices = invoices.filter(status=status)
    else:
        status = "all"
    return page(
        request,
        "invoices",
        invoices=Paginator(invoices, 30).get_page(request.GET.get("page")),
        status=status,
    )


@access(require_admin)
def void_invoice(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    form = forms.ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
            if invoice.status == Invoice.Status.OPEN:
                invoice.status = Invoice.Status.VOID
                invoice.closed_at = timezone.now()
                invoice.save(update_fields=["status", "closed_at"])
                audit(request.user, "invoice.void", invoice, form.cleaned_data["reason"])
                messages.success(request, "Invoice voided. Recorded payment evidence is retained.")
        return redirect("moon_rental:invoices")
    return page(
        request,
        "form",
        form=form,
        title=f"Void {invoice.code}",
        submit="Void invoice",
        help_text="Voiding stops collection of this invoice. It does not refund any partial payment.",
    )


@access(require_admin)
def wallet_list(request):
    return page(request, "wallets", accounts=WalletAccount.objects.all())


def finish_wallet(request, token, kind):
    try:
        account = connect_wallet(actor=request.user, token=token, kind=kind)
        messages.success(request, f"Connected {account.name}. Wallet access was verified with ESI.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception:
        messages.error(
            request,
            "ESI could not verify this wallet. Check the character's wallet scope and corporation role, then retry.",
        )
    return redirect("moon_rental:wallets")


@access(require_admin)
@token_required(scopes=SCOPES[WalletAccount.Kind.CHARACTER])
def connect_character(request, token):
    return finish_wallet(request, token, WalletAccount.Kind.CHARACTER)


@access(require_admin)
@token_required(scopes=SCOPES[WalletAccount.Kind.CORPORATION])
def connect_corporation(request, token):
    return finish_wallet(request, token, WalletAccount.Kind.CORPORATION)


@access(require_admin)
@require_POST
def wallet_action(request, account_id):
    account = get_object_or_404(WalletAccount, pk=account_id)
    try:
        if request.POST.get("action") == "default":
            set_default_wallet(actor=request.user, account_id=account.pk)
        elif request.POST.get("action") == "toggle":
            toggle_wallet(actor=request.user, account_id=account.pk)
        elif request.POST.get("action") == "scan":
            tasks.scan_wallets.delay()
            messages.success(request, "Wallet scans queued.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("moon_rental:wallets")


@access(require_admin)
@require_POST
def sync(request):
    count = sync_moons(actor=request.user)
    messages.success(request, f"Synced {count} moons from moonmining.")
    return redirect("moon_rental:moons")
