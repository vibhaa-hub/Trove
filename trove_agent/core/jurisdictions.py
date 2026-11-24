"""Country rules profiles: legal documents, policy defaults, privacy regime, deadlines, residency.

Each case has a `country` code (IN, UK, US, EU, OTHER). The profile drives:
* which legal documents a release request must carry (a release gate),
* default and minimum release-policy values,
* the privacy notice and data-subject rights shown to owners and nominees,
* key deadlines, computed from the date of death where possible,
* official asset-tracing services used by Discovery,
* where the data should be hosted.

This is organisational guidance compiled from public sources, not legal advice. Every profile
carries `last_reviewed`; have a lawyer in each country review the content before real use.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

LAST_REVIEWED = "2026-11"
DISCLAIMER = ("Guidance only, compiled from public sources; not legal advice. Rules change and "
              "vary by state/region. Confirm with a lawyer or the relevant authority.")


@dataclass(frozen=True)
class LegalRequirement:
    key: str
    label: str
    why: str
    accepted: tuple[str, ...]  # any one of these satisfies the requirement


@dataclass(frozen=True)
class Deadline:
    label: str
    detail: str
    days_after_death: int | None = None
    months_after_death: int | None = None
    end_of_month: bool = False  # e.g. UK inheritance tax: end of the sixth month


@dataclass(frozen=True)
class Privacy:
    law: str
    regulator: str
    rights: tuple[str, ...]
    retention: str
    transfers: str
    deceased_data: str
    notice: str
    contact_requirement: str


@dataclass(frozen=True)
class Profile:
    code: str
    name: str
    succession: str  # how entitlement works, in one paragraph
    legal_requirements: tuple[LegalRequirement, ...]
    legal_documents_default: str  # "required" | "advisory"
    defaults: dict[str, int]  # quorum, waiting_period_hours, checkin_interval_days
    minimums: dict[str, int]  # enforced in production
    privacy: Privacy
    deadlines: tuple[Deadline, ...]
    official_tracing: dict[str, tuple[str, ...]]
    data_region: str  # recommended hosting region code
    residency_note: str
    states_label: str = "State / region"
    examples: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name, "data_region": self.data_region,
                "states_label": self.states_label, "examples": list(self.examples)}

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["last_reviewed"] = LAST_REVIEWED
        data["disclaimer"] = DISCLAIMER
        return data


GDPR_RIGHTS = (
    "Be informed (this notice)", "Access a copy of your data", "Rectification", "Erasure",
    "Restriction of processing", "Data portability", "Object to processing",
    "Withdraw consent at any time", "Complain to the supervisory authority",
)

PROFILES: dict[str, Profile] = {
    "IN": Profile(
        code="IN", name="India",
        succession=(
            "A nominee registered with a bank, insurer, depository or EPFO can usually receive the "
            "asset, but for most assets the nominee holds it in trust for the legal heirs under the "
            "applicable personal succession law (Hindu Succession Act 1956, Indian Succession Act "
            "1925, Muslim personal law, etc.). Exceptions exist, e.g. 'beneficial nominees' "
            "(parents, spouse, children) under section 39 of the Insurance Act 1938 as amended in "
            "2015. Institutions often ask for a succession or legal-heir certificate above their "
            "own value thresholds."),
        legal_requirements=(
            LegalRequirement(
                key="entitlement", label="Proof of legal entitlement",
                why="Shows who the legal heirs or estate representatives are, beyond the nominee "
                    "registration.",
                accepted=("Legal heir certificate (Tahsildar / revenue authority)",
                          "Succession certificate (civil court, Indian Succession Act s.372)",
                          "Probate of will / letters of administration",
                          "Family settlement or no-objection affidavit from all legal heirs")),
        ),
        legal_documents_default="required",
        defaults={"quorum": 2, "waiting_period_hours": 72, "checkin_interval_days": 90},
        minimums={"waiting_period_hours": 48},
        privacy=Privacy(
            law="Digital Personal Data Protection Act 2023 (DPDP Act) and DPDP Rules 2025",
            regulator="Data Protection Board of India",
            rights=("Be informed through a clear notice before consent",
                    "Access a summary of your personal data and processing",
                    "Correction, completion, updating and erasure",
                    "Grievance redressal (contact the grievance officer first)",
                    "Nominate a person to exercise your rights after death or incapacity (s.14)",
                    "Withdraw consent as easily as it was given"),
            retention="Erase data once the purpose is served or consent is withdrawn, unless a "
                      "law requires retention.",
            transfers="Cross-border transfer is allowed except to countries the Government "
                      "restricts by notification.",
            deceased_data="The Act lets a person nominate someone to exercise their data rights "
                          "on death; Trove nominees can be recorded for this purpose.",
            notice="Trove processes your personal data (contact details, identity hints stored as "
                   "keyed hashes, uploaded documents) to verify nominees and release the legacy "
                   "vault, based on your consent. You may access, correct or erase your data, "
                   "withdraw consent, and nominate a person to exercise these rights.",
            contact_requirement="Publish a grievance officer / contact person who responds to "
                                "data-principal requests."),
        deadlines=(
            Deadline("Register the death", "Report to the local registrar under the Registration "
                     "of Births and Deaths Act 1969 (later registration needs extra steps).",
                     days_after_death=21),
            Deadline("Deceased's income-tax return", "Legal representative files the return for "
                     "the year of death by the normal due date (usually 31 July of the next "
                     "year for individuals)."),
            Deadline("Unclaimed money", "Bank deposits unclaimed for 10 years move to the RBI "
                     "Depositor Education and Awareness Fund; unclaimed insurance moves to the "
                     "Senior Citizens' Welfare Fund. Both stay claimable."),
        ),
        official_tracing={
            "bank_account": ("RBI UDGAM portal — search unclaimed deposits across banks (udgam.rbi.org.in)",
                             "Income-tax e-filing portal: register as legal heir, then view AIS / Form 26AS "
                             "for interest-paying banks"),
            "insurance": ("Each insurer's 'unclaimed amounts' search (policy no./name + DOB + PAN)",
                          "LIC: unclaimed amounts search on licindia.in", "Bima Bharosa / IRDAI grievance portal"),
            "investment": ("MF Central / CAMS / KFintech consolidated account statement (by PAN)",
                           "NSDL / CDSL consolidated account statement for demat holdings",
                           "IEPF (iepf.gov.in) for unclaimed dividends and shares transferred by companies"),
            "pension": ("EPFO member passbook / UAN with the employer", "NPS: contact the CRA with PRAN for death claim"),
            "property": ("State land-records portal / sub-registrar office", "Municipal property-tax records"),
        },
        data_region="in", residency_note="Host in India where possible; transfers abroad are "
                                         "permitted unless the destination is restricted.",
        examples=("Maharashtra", "Karnataka", "Delhi"),
    ),
    "UK": Profile(
        code="UK", name="United Kingdom",
        succession=(
            "The estate is administered by a personal representative: an executor named in the "
            "will (grant of probate) or an administrator under the intestacy rules (letters of "
            "administration). Scotland uses Confirmation. Small estates may be released by some "
            "banks without a grant, at their discretion."),
        legal_requirements=(
            LegalRequirement(
                key="grant", label="Grant of representation",
                why="Institutions release assets to the personal representative named in the grant.",
                accepted=("Grant of probate (England & Wales / Northern Ireland)",
                          "Letters of administration",
                          "Confirmation (Scotland)",
                          "Institution's small-estate indemnity form, where offered")),
        ),
        legal_documents_default="required",
        defaults={"quorum": 2, "waiting_period_hours": 72, "checkin_interval_days": 90},
        minimums={"waiting_period_hours": 24},
        privacy=Privacy(
            law="UK GDPR and Data Protection Act 2018",
            regulator="Information Commissioner's Office (ICO)",
            rights=GDPR_RIGHTS,
            retention="Keep personal data no longer than necessary for the purpose; document the "
                      "retention period.",
            transfers="Transfers outside the UK need UK adequacy regulations, an IDTA or the UK "
                      "Addendum to EU SCCs.",
            deceased_data="UK GDPR protects living people only; owners' and nominees' data is "
                          "covered while they are alive. Duty of confidence may still apply.",
            notice="Trove processes your personal data to verify nominees and administer the "
                   "legacy vault. Lawful bases: consent and legitimate interests. You have the "
                   "rights listed below and may complain to the ICO.",
            contact_requirement="Name a data protection contact (and a DPO where required) and "
                                "pay the ICO data protection fee."),
        deadlines=(
            Deadline("Register the death", "England, Wales and Northern Ireland: 5 days. "
                     "Scotland: 8 days.", days_after_death=5),
            Deadline("Pay Inheritance Tax", "Due by the end of the sixth month after the death "
                     "(interest accrues after that).", months_after_death=6, end_of_month=True),
            Deadline("Inheritance Tax account", "Send the IHT account within 12 months of the death.",
                     months_after_death=12),
            Deadline("Family provision claims", "Claims under the Inheritance (Provision for Family "
                     "and Dependants) Act 1975: 6 months from the grant."),
        ),
        official_tracing={
            "bank_account": ("My Lost Account (mylostaccount.org.uk) — free search of banks and building societies",),
            "pension": ("Pension Tracing Service (gov.uk/find-pension-contact-details)",),
            "insurance": ("Unclaimed Assets Register (paid search covering policies, pensions, shares)",),
            "investment": ("Unclaimed Assets Register", "Contact share registrars (Computershare, Equiniti, Link)"),
            "property": ("HM Land Registry title search",),
            "digital_account": ("Tell Us Once (gov.uk) to notify government departments",),
        },
        data_region="uk", residency_note="Host in the UK or an adequate country (e.g. the EU).",
        states_label="Nation", examples=("England", "Scotland", "Wales", "Northern Ireland"),
    ),
    "US": Profile(
        code="US", name="United States",
        succession=(
            "Probate is state law. A court appoints an executor (letters testamentary) or an "
            "administrator (letters of administration). Many states allow a small-estate affidavit "
            "below a value threshold. Payable-on-death / transfer-on-death designations and "
            "beneficiary designations pass outside probate. Digital assets: most states have "
            "adopted RUFADAA."),
        legal_requirements=(
            LegalRequirement(
                key="authority", label="Court authority or small-estate affidavit",
                why="Shows who may act for the estate under state law.",
                accepted=("Letters testamentary", "Letters of administration",
                          "Small-estate affidavit (state threshold applies)",
                          "Beneficiary / POD / TOD designation confirmation from the institution")),
        ),
        legal_documents_default="required",
        defaults={"quorum": 2, "waiting_period_hours": 72, "checkin_interval_days": 90},
        minimums={"waiting_period_hours": 24},
        privacy=Privacy(
            law="No single federal law: state privacy laws (e.g. California CCPA/CPRA, Virginia "
                "VCDPA, Colorado CPA) plus sector laws",
            regulator="State attorneys general / California Privacy Protection Agency; FTC (unfair practices)",
            rights=("Know what is collected and why", "Access and portability", "Correction",
                    "Deletion", "Opt out of sale/sharing and targeted advertising",
                    "Non-discrimination for exercising rights"),
            retention="Disclose retention periods (required in California) and delete when no "
                      "longer needed.",
            transfers="No general transfer restriction; contractual and state requirements apply.",
            deceased_data="Fiduciary access to a deceased user's digital assets follows RUFADAA "
                          "in most states.",
            notice="Trove processes your personal information to verify nominees and release the "
                   "legacy vault. We do not sell or share it for advertising. Depending on your "
                   "state you can know, access, correct and delete your data.",
            contact_requirement="Offer at least two request methods (e.g. web form and email) "
                                "where state law requires."),
        deadlines=(
            Deadline("File the will with the probate court", "Most states require the custodian "
                     "to lodge the will promptly (often within 30 days).", days_after_death=30),
            Deadline("Federal estate tax return (Form 706)", "Due 9 months after death if the estate "
                     "exceeds the exemption or to elect portability; extension available.",
                     months_after_death=9),
            Deadline("Final individual return (Form 1040)", "Due April 15 of the year after death."),
            Deadline("Creditor claims", "Usually 3–6 months after notice to creditors; varies by state."),
        ),
        official_tracing={
            "bank_account": ("MissingMoney.com / unclaimed.org — state unclaimed property databases",),
            "insurance": ("NAIC Life Insurance Policy Locator",),
            "pension": ("PBGC unclaimed retirement benefits search", "National Registry of Unclaimed Retirement Benefits"),
            "investment": ("State unclaimed property (securities)", "TreasuryDirect for savings bonds"),
            "property": ("County recorder / assessor records",),
        },
        data_region="us", residency_note="Host in the US; no general localisation requirement.",
        states_label="State", examples=("California", "New York", "Texas"),
    ),
    "EU": Profile(
        code="EU", name="European Union",
        succession=(
            "The EU Succession Regulation (650/2012) applies the law of the deceased's last habitual "
            "residence (unless they chose their nationality's law) for deaths from 17 August 2015. "
            "A European Certificate of Succession proves heirs' and administrators' status in other "
            "member states; national certificates (e.g. German Erbschein, French acte de "
            "notoriété) are used at home. Heirs can usually accept or disclaim within national time limits."),
        legal_requirements=(
            LegalRequirement(
                key="succession_certificate", label="Certificate of succession",
                why="Proves who inherits or administers the estate.",
                accepted=("European Certificate of Succession (Regulation 650/2012)",
                          "National certificate of inheritance (e.g. Erbschein, acte de notoriété, "
                          "verklaring van erfrecht)",
                          "Notarial deed of succession")),
        ),
        legal_documents_default="required",
        defaults={"quorum": 2, "waiting_period_hours": 72, "checkin_interval_days": 90},
        minimums={"waiting_period_hours": 24},
        privacy=Privacy(
            law="General Data Protection Regulation (EU) 2016/679 (GDPR)",
            regulator="National supervisory authority of the member state (lead authority for cross-border processing)",
            rights=GDPR_RIGHTS,
            retention="Storage limitation: keep data no longer than necessary and document the period.",
            transfers="Transfers outside the EEA need an adequacy decision, SCCs or another "
                      "Article 46 safeguard.",
            deceased_data="GDPR covers living people, but some member states protect deceased "
                          "persons' data (e.g. France: post-mortem digital directives; Italy: "
                          "rights exercisable by heirs).",
            notice="Trove processes your personal data to verify nominees and administer the "
                   "legacy vault. Lawful bases: consent and legitimate interests. You have the "
                   "rights listed below and may complain to your supervisory authority.",
            contact_requirement="Name a controller contact and a DPO where Article 37 requires; "
                                "appoint an EU representative if established outside the EU."),
        deadlines=(
            Deadline("Register the death", "Usually within days (e.g. France 24 hours, Germany "
                     "by the next working day); check the member state.", days_after_death=3),
            Deadline("Disclaim the inheritance (example: Germany)", "6 weeks from knowing of the "
                     "inheritance (§1944 BGB); other states differ.", days_after_death=42),
            Deadline("Inheritance tax return", "National deadlines differ (e.g. France: 6 months "
                     "for deaths in France)."),
        ),
        official_tracing={
            "bank_account": ("National dormant-account searches (e.g. France Ciclade, Belgium "
                             "Dormant accounts, Netherlands via the bank association)",),
            "insurance": ("National life-insurance search services (e.g. France AGIRA, Italy "
                          "IVASS enquiry service)",),
            "property": ("National land registry / cadastre",),
            "trust": ("National wills registers via the European Network of Registers of Wills",),
        },
        data_region="eu", residency_note="Host in the EU/EEA or an adequate country.",
        states_label="Member state", examples=("Germany", "France", "Ireland"),
    ),
    "OTHER": Profile(
        code="OTHER", name="Other / not listed",
        succession="Succession rules depend on the country. Ask a local lawyer which court or "
                   "government document proves who may receive the estate.",
        legal_requirements=(
            LegalRequirement(
                key="entitlement", label="Document proving entitlement",
                why="Most institutions need a court or government document naming heirs or "
                    "administrators.",
                accepted=("Court grant or order", "Government heirship certificate",
                          "Notarial deed of succession")),
        ),
        legal_documents_default="advisory",
        defaults={"quorum": 2, "waiting_period_hours": 72, "checkin_interval_days": 90},
        minimums={"waiting_period_hours": 24},
        privacy=Privacy(
            law="Local data protection law (if any) — Trove applies GDPR-level controls by default",
            regulator="Local data protection authority",
            rights=("Access", "Correction", "Deletion", "Withdraw consent"),
            retention="Kept only as long as needed for the vault and its audit trail.",
            transfers="Check local rules before hosting abroad.",
            deceased_data="Check local law.",
            notice="Trove processes your personal data to verify nominees and release the legacy "
                   "vault, with your consent. You may access, correct or delete it.",
            contact_requirement="Provide a privacy contact."),
        deadlines=(),
        official_tracing={},
        data_region="any", residency_note="Check local data-localisation rules.",
        states_label="Country / region",
    ),
}

COUNTRIES = tuple(PROFILES)

# Exact names/aliases only. Whole-string matching avoids the old bug where any word "in"
# (e.g. "Living in Canada") was read as India.
_ALIASES = {
    "IN": {"in", "ind", "india", "bharat", "republic of india"},
    "UK": {"uk", "gb", "gbr", "united kingdom", "great britain", "britain", "england", "scotland",
           "wales", "northern ireland", "england and wales"},
    "US": {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "america"},
    "EU": {"eu", "european union", "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia",
           "czech republic", "denmark", "estonia", "finland", "france", "germany", "greece", "hungary",
           "ireland", "italy", "latvia", "lithuania", "luxembourg", "malta", "netherlands", "poland",
           "portugal", "romania", "slovakia", "slovenia", "spain", "sweden"},
}
_US_STATES = {"alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
              "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
              "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
              "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
              "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
              "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
              "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
              "washington", "west virginia", "wisconsin", "wyoming", "district of columbia"}
_IN_STATES = {"andhra pradesh", "assam", "bihar", "delhi", "goa", "gujarat", "haryana", "karnataka",
              "kerala", "madhya pradesh", "maharashtra", "odisha", "punjab", "rajasthan",
              "tamil nadu", "telangana", "uttar pradesh", "west bengal"}


def detect_country(text: str | None) -> str:
    """Best-effort code for legacy free-text jurisdictions. Matches whole names, not words."""
    if not text:
        return "OTHER"
    cleaned = re.sub(r"\s+", " ", text.strip().casefold())
    parts = [p.strip(" .") for p in re.split(r"[,;/()]", cleaned) if p.strip(" .")]
    for candidate in [cleaned.strip(" ."), *reversed(parts)]:
        for code, names in _ALIASES.items():
            if candidate in names:
                return code
        if candidate in _US_STATES:
            return "US"
        if candidate in _IN_STATES:
            return "IN"
    return "OTHER"


def profile(code: str | None) -> Profile:
    return PROFILES.get((code or "OTHER").upper(), PROFILES["OTHER"])


def normalize_country(value: Any) -> str:
    if not isinstance(value, str) or value.strip().upper() not in PROFILES:
        raise ValueError(f"country must be one of {', '.join(COUNTRIES)}")
    return value.strip().upper()


def _add_months(day: date, months: int, end_of_month: bool) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, last if end_of_month else min(day.day, last))


def deadlines(code: str, date_of_death: str | None) -> list[dict[str, Any]]:
    """Deadlines for a profile, with due dates computed when the date of death is known."""
    from datetime import timedelta

    dod: date | None = None
    if date_of_death:
        try:
            dod = date.fromisoformat(date_of_death[:10])
        except ValueError:
            dod = None
    today = date.today()
    result = []
    for item in profile(code).deadlines:
        due = None
        if dod and item.days_after_death is not None:
            due = dod + timedelta(days=item.days_after_death)
        elif dod and item.months_after_death is not None:
            due = _add_months(dod, item.months_after_death, item.end_of_month)
        result.append({
            "label": item.label, "detail": item.detail,
            "due": due.isoformat() if due else None,
            "overdue": bool(due and due < today),
            "days_left": (due - today).days if due else None,
        })
    return result


def catalog() -> list[dict[str, Any]]:
    return [p.summary() for p in PROFILES.values()]
