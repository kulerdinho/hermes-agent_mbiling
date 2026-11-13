'use strict'
/**
 * Rôle de l'agent pour essai_navigateur.py (ne s'utilise pas seul) : se connecte au navigateur
 * du PC À TRAVERS le Relais (adresse CDP de la cellule), pilote une page et mesure les délais.
 *
 *   node essai_navigateur_agent.cjs <adresse CDP> <page de l'essai> <adresse interdite>
 *
 * Écrit une ligne JSON de résultats sur la sortie standard.
 */
const path = require('node:path')
const { createRequire } = require('node:module')

const [cdp, page, interdite] = process.argv.slice(2)
const { chromium } = createRequire(path.join(__dirname, '..', '..', 'apps', 'desktop', 'package.json'))('playwright')

async function chrono(fn) {
  const debut = performance.now()
  const valeur = await fn()
  return { valeur, ms: Math.round(performance.now() - debut) }
}

;(async () => {
  const r = { mesures_ms: {} }
  const connexion = await chrono(() => chromium.connectOverCDP(cdp))
  r.mesures_ms.connexion = connexion.ms
  const navigateur = connexion.valeur
  const contexte = navigateur.contexts()[0] || (await navigateur.newContext())
  const onglet = await contexte.newPage()

  r.mesures_ms.ouvrir_page = (await chrono(() => onglet.goto(page))).ms
  const titre = await chrono(() => onglet.title())
  r.titre = titre.valeur
  r.mesures_ms.lire_titre = titre.ms
  r.mesures_ms.cliquer = (await chrono(() => onglet.click('#bouton'))).ms
  r.apres_clic = await onglet.textContent('#etat')
  r.mesures_ms.taper = (await chrono(() => onglet.fill('#champ', 'Bonjour depuis le Cloud'))).ms
  r.texte_tape = await onglet.inputValue('#champ')
  r.mesures_ms.lire_page = (await chrono(() => onglet.evaluate(() => document.body.innerText))).ms

  // Aller-retour simple (une commande CDP sans effet), répété pour une valeur stable.
  const tours = []
  for (let i = 0; i < 10; i++) tours.push((await chrono(() => onglet.evaluate(() => 1))).ms)
  tours.sort((a, b) => a - b)
  r.mesures_ms.aller_retour_median = tours[Math.floor(tours.length / 2)]

  try {
    await onglet.goto(interdite, { timeout: 15000 })
    r.interdit = 'OUVERT'
  } catch (e) {
    r.interdit = String(e.message || e).split('\n')[0]
  }
  await onglet.close()
  await navigateur.close()
  console.log(JSON.stringify(r))
})().catch(e => {
  console.log(JSON.stringify({ erreur: String(e && e.message ? e.message : e).split('\n')[0] }))
  process.exit(1)
})
