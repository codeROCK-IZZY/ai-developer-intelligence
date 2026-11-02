package main

import (
	"encoding/json"
	"log"
	"net/http"
	"strconv"

	"github.com/fernwood/shipping/rates"
	"github.com/fernwood/shipping/tracking"
)

func main() {
	http.HandleFunc("/rates", handleRates)
	http.HandleFunc("/tracking", handleTracking)
	log.Fatal(http.ListenAndServe(":8081", nil))
}

// handleRates answers GET /rates?weight=<grams>&to=<region>&speed=<standard|express|overnight>.
func handleRates(w http.ResponseWriter, r *http.Request) {
	weight, err := strconv.Atoi(r.URL.Query().Get("weight"))
	if err != nil {
		http.Error(w, "weight must be an integer", http.StatusBadRequest)
		return
	}
	rate, err := rates.CalculateRate(weight, r.URL.Query().Get("to"), r.URL.Query().Get("speed"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnprocessableEntity)
		return
	}
	json.NewEncoder(w).Encode(map[string]int{"rate_cents": rate})
}

// handleTracking answers GET /tracking?number=<tracking number>.
func handleTracking(w http.ResponseWriter, r *http.Request) {
	events, err := tracking.TrackShipment(r.URL.Query().Get("number"))
	if err != nil {
		http.Error(w, "unknown shipment", http.StatusNotFound)
		return
	}
	json.NewEncoder(w).Encode(events)
}
