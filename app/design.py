"""Shared visual language for the case study and working product."""
from html import escape
from pathlib import Path
import streamlit as st


def apply_theme() -> None:
    st.markdown(f"<style>{Path(__file__).with_name('theme.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def html(body: str) -> None:
    st.markdown(body, unsafe_allow_html=True)


def go(page: str) -> None:
    st.session_state['page'] = page


def section(number: str, title: str, description: str = '') -> None:
    html(f'<div class="section-heading"><span>{escape(number)}</span><div><h2>{escape(title)}</h2><p>{escape(description)}</p></div></div>')


def page_header(eyebrow: str, title: str, description: str) -> None:
    html(f'<div class="page-heading"><p class="eyebrow">{escape(eyebrow)}</p><h1>{escape(title)}</h1><p class="page-lede">{escape(description)}</p></div>')


def card(kicker: str, title: str, body: str) -> str:
    return f'<article class="editorial-card"><p class="eyebrow">{escape(kicker)}</p><h3>{escape(title)}</h3><p>{escape(body)}</p></article>'


def footer() -> None:
    html('<footer class="portfolio-footer"><span><b>VQI</b> / Vendor Quote Intelligence</span><span>Independent product prototype · Synthetic sourcing data</span></footer>')
