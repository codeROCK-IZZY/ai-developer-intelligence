// Package tracking looks up the scan history of shipments.
package tracking

import (
	"database/sql"
	"fmt"
)

// DB is the tracking database, opened by main.
var DB *sql.DB

// Event is one scan of a parcel.
type Event struct {
	Time     string `json:"time"`
	Location string `json:"location"`
	Status   string `json:"status"`
}

// TrackShipment returns the scan history of a shipment, newest first.
func TrackShipment(number string) ([]Event, error) {
	rows, err := DB.Query(fmt.Sprintf("SELECT time, location, status FROM events WHERE number = '%s' ORDER BY time DESC", number))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var events []Event
	for rows.Next() {
		var e Event
		if err := rows.Scan(&e.Time, &e.Location, &e.Status); err != nil {
			return nil, err
		}
		events = append(events, e)
	}
	return events, rows.Err()
}
