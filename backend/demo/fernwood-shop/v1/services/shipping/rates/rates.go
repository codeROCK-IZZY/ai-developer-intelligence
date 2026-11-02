// Package rates prices parcels by weight, destination zone and delivery speed.
package rates

import "errors"

// zones maps destination regions to price zones (1 = closest to the warehouse).
var zones = map[string]int{"US-CA": 1, "US-OR": 1, "US-WA": 1, "US-TX": 2, "US-NY": 3, "CA-ON": 4, "CA-BC": 4}

// CalculateRate returns the shipping price in cents for a parcel.
func CalculateRate(weightGrams int, destination string, speed string) (int, error) {
	if weightGrams <= 0 {
		return 0, errors.New("weight must be positive")
	}
	zone, ok := zones[destination]
	if !ok {
		return 0, errors.New("unsupported destination")
	}
	base := 500
	switch {
	case weightGrams <= 500:
		base += 0
	case weightGrams <= 2000:
		base += 400
	case weightGrams <= 5000:
		base += 900
	case weightGrams <= 20000:
		base += 2000
	default:
		return 0, errors.New("parcel too heavy")
	}
	base += zone * 150
	switch speed {
	case "express":
		base = base * 2
	case "overnight":
		if zone > 2 {
			return 0, errors.New("overnight delivery is not available for this destination")
		}
		base = base * 3
	case "", "standard":
	default:
		return 0, errors.New("unknown delivery speed")
	}
	if weightGrams > 10000 && zone >= 3 {
		base += 1500
	}
	return base, nil
}
