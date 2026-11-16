#!/usr/bin/env ruby
# frozen_string_literal: true

require "json"
require "nokogiri"
require "open-uri"
require "uri"

ROOT = File.expand_path("..", __dir__)
OUTPUT = File.join(ROOT, "webdata", "clinicas.json")

SITES = {
  # Original June-17 sites
  "pinamed" => "https://clinica-pinamed.brila.ai",
  "clinica-del-bosque" => "https://clinica-del-bosque.brila.ai",
  "cefem" => "https://cefem.brila.ai",
  "cem" => "https://cem-pinamar.brila.ai",
  "dimap" => "https://dimap.brila.ai",
  "laboratorio-katzman" => "https://laboratorio-katzman.brila.ai",
  "laboratorio-del-bosque" => "https://laboratorio-del-bosque.brila.ai",
  "integral-vision-clinic" => "https://integral-vision-clinic.brila.ai",
  # June-20 batch — confirmed correct content
  "centro-eqis" => "https://centro-eqis.brila.ai",
  "shanti-talleres" => "https://shanti.brila.ai",
  "consultorios-del-mar" => "https://consultorios-del-mar.brila.ai",
  "sensoria" => "https://sensoria.brila.ai",
  "shanti-centro-integral-de-salud" => "https://shanti-1.brila.ai",
  "jb-consultorios-carilo" => "https://jb-consultorios-carilo.brila.ai",
  "punto-pediatrico-pinamar" => "https://punto-pediatrico-pinamar.brila.ai",
  "consultorio-mar-de-ostende" => "https://consultorios-mar-de-ostende.brila.ai",
  "consultorio-de-la-mujer" => "https://consultorio-de-la-mujer.brila.ai",
  "cardenas-odontologia" => "https://cardenas-odontologia.brila.ai",
  "creser-espacio-psicopedagogico" => "https://espacio-creser.brila.ai",
  "tu-espacio-psi" => "https://espacio-psi.brila.ai",
  "clinica-del-sol" => "https://physical-therapist-del-sol.brila.ai",
  "capmar-psicologia-clinica-y-deportiva" => "https://dispensario-caps.brila.ai",
  "odontologia-casales-maria-florencia" => "https://odontologia-manuela-kaisser.brila.ai",
  "claudia-lorena-gomez-psiquiatra" => "https://consultorio-psiquiatrico-garibaldi.brila.ai",
  "cis-odontologia" => "https://cis.brila.ai",
  # Skipped — wrong Maps URL resolves to different business (needs clinic-maps-urls.json fix):
  # "consultorios-del-pinar" => nil,   # → Clinica Pinamed content
  # "gynes" => nil,                    # → Centro EQIS content
  # "obesidad-y-metabolismo" => nil,   # → Clinica Pinamed content
  # "instituto-oftalmologico-gesell" => nil  # → Integral Vision Clinic content
}.freeze

DISPLAY_NAMES = {
  "pinamed" => "Pinamed",
  "clinica-del-bosque" => "Clínica del Bosque",
  "cefem" => "CEFEM",
  "cem" => "CEM Pinamar",
  "dimap" => "DIMAP",
  "laboratorio-katzman" => "Laboratorio Katzman",
  "laboratorio-del-bosque" => "Laboratorio del Bosque",
  "integral-vision-clinic" => "Integral Vision Clinic",
  "centro-eqis" => "Centro EQIS",
  "shanti-talleres" => "Shanti - Talleres",
  "consultorios-del-mar" => "Consultorios del Mar",
  "sensoria" => "Sensoria",
  "shanti-centro-integral-de-salud" => "Shanti - Centro Integral de Salud",
  "jb-consultorios-carilo" => "JB Consultorios Cariló",
  "punto-pediatrico-pinamar" => "Punto Pediátrico Pinamar",
  "consultorio-mar-de-ostende" => "Consultorios Mar de Ostende",
  "consultorio-de-la-mujer" => "Consultorio de la Mujer",
  "cardenas-odontologia" => "Cárdenas Odontología",
  "creser-espacio-psicopedagogico" => "CreSer - Espacio Psicopedagógico",
  "tu-espacio-psi" => "Tu Espacio PSI",
  "clinica-del-sol" => "Clínica del Sol",
  "capmar-psicologia-clinica-y-deportiva" => "CAPMAR - Psicología Clínica y Deportiva",
  "odontologia-casales-maria-florencia" => "Odontología Casales - Dra. María Florencia",
  "claudia-lorena-gomez-psiquiatra" => "Dra. Claudia Lorena Gómez - Psiquiatra",
  "cis-odontologia" => "CIS - Odontología"
}.freeze

ALIASES = {
  "cempi-centro-medico-pinamar" => {
    "name" => "CEMPI Centro Médico Pinamar",
    "slug" => "cempi-centro-medico-pinamar",
    "alias_of" => "cem"
  }
}.freeze

def clean_text(node)
  return "" unless node

  node.text.gsub(/\s+/, " ").strip
end

def strip_quote(text)
  text.to_s.strip.sub(/\A[\"“]/, "").sub(/[\"”]\z/, "")
end

def number_from(text)
  text.to_s.tr(",", ".").match(/\d+(?:\.\d+)?/)&.[](0)
end

def card_data(node)
  quote_node = node.at_css("blockquote p")
  description_node = node.at_css(".advantage-desc") || node.xpath("./p[not(ancestor::blockquote)]").first
  author = clean_text(node.at_css("cite"))
  data = {
    "title" => clean_text(node.at_css("h4")),
    "description" => strip_quote(clean_text(description_node)),
    "quote" => strip_quote(clean_text(quote_node)),
    "author" => author,
    "author_photo" => node.at_css("cite img")&.[]("src").to_s
  }
  data.reject { |_key, value| value.nil? || value.empty? }
end

def section_data(section)
  cards = section.css(".advantage-card, .card").map { |card| card_data(card) }
  {
    "id" => section["id"].to_s,
    "title" => clean_text(section.at_css("h2")),
    "subtitle" => clean_text(section.at_css(".section-subtitle")),
    "items" => cards
  }.reject { |_key, value| value.respond_to?(:empty?) && value.empty? }
end

def fetch_site(slug, url)
  html = URI.open(url, "User-Agent" => "Sacoturno clinic sync/1.0", read_timeout: 30).read
  doc = Nokogiri::HTML(html)
  hero = doc.at_css("section.hero")
  hero_link = hero&.at_css(".hero-image a")
  gallery = []
  if hero_link
    gallery << {
      "url" => hero_link["href"].to_s,
      "title" => hero_link["data-title"].to_s.strip
    }
  end
  doc.css(".gallery-item a").each do |link|
    gallery << {
      "url" => link["href"].to_s,
      "title" => link["data-title"].to_s.strip
    }
  end
  gallery.reject! { |item| item["url"].empty? }
  gallery.uniq! { |item| item["url"] }

  rating_raw = number_from(clean_text(doc.at_css(".rating-score")))
  reviews_raw = number_from(clean_text(doc.at_css(".rating-count")))
  phone_link = doc.at_css('a[href^="tel:"]')
  maps_link = doc.at_css('a[href*="google.com/maps/dir"], a[href*="maps.google.com/maps/dir"]')
  hours = doc.css(".hours-day").map do |day|
    {
      "days" => clean_text(day.at_css(".hours-day-name")),
      "hours" => clean_text(day.at_css(".hours-day-time"))
    }
  end.reject { |row| row.values.any?(&:empty?) }

  sections = doc.css("section").map do |section|
    next if %w[home gallery hours].include?(section["id"].to_s)
    next unless section.at_css("h2")

    section_data(section)
  end.compact

  name = DISPLAY_NAMES[slug] || clean_text(doc.at_css(".logo"))
  name = clean_text(doc.at_css("title")) if name.empty?
  entry = {
    "slug" => slug,
    "name" => name,
    "brila_url" => url,
    "title" => clean_text(hero&.at_css("h1")),
    "subtitle" => clean_text(hero&.at_css(".subtitle")),
    "address" => clean_text(hero&.at_css(".location-chip")),
    "phone" => clean_text(phone_link),
    "google_maps_url" => maps_link&.[]("href").to_s,
    "google_rating" => rating_raw&.to_f,
    "reviews_count" => reviews_raw&.to_i,
    "schedule" => hours,
    "photos" => gallery.map { |item| item["url"] },
    "photo_titles" => gallery,
    "sections" => sections
  }
  entry.reject { |_key, value| value.nil? || (value.respond_to?(:empty?) && value.empty?) }
end

existing = File.exist?(OUTPUT) ? JSON.parse(File.read(OUTPUT)) : {}
result = existing.reject { |_slug, clinic| clinic.is_a?(Hash) && clinic["brila_url"] }

SITES.each do |slug, url|
  warn "Syncing #{url}"
  result[slug] = fetch_site(slug, url)
end
ALIASES.each { |slug, entry| result[slug] = entry }

ordered = result.sort.to_h
File.write(OUTPUT, JSON.pretty_generate(ordered) + "\n")
warn "Saved #{SITES.length} Brila clinics to #{OUTPUT}"
