#!/bin/bash

curl 'http://localhost:5000/?p0=webcal%3A//abo.kleiner-kalender.de/ical/c28-zeitumstellung.ics&p1=webcal%3A//abo.kleiner-kalender.de/ical/c17-jahreszeiten.ics'
curl 'http://localhost:5000/filter?src=webcal%3A%2F%2Fabo.kleiner-kalender.de%2Fical%2Fc17-jahreszeiten.ics&d=remove&f0=keep:SUMMARY:contains:fr%C3%BChling'
