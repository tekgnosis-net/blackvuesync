Feature: Resume interrupted downloads

  Scenario: Resume a partially downloaded recording
    Given these recordings on the dashcam:
      | filename               |
      | 20230101_120000_NF.mp4 |
    Given a partial download of "20230101_120000_NF.mp4"
    When blackvuesync runs
    Then the recording "20230101_120000_NF.mp4" is fully downloaded
